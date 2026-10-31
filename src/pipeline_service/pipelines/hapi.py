import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Literal

from pymongo.database import Database
from sunpy.timeseries import GenericTimeSeries

from pipeline_service.database.mongo_loader import MongoLoader
from pipeline_service.parsing.hapi import convert_hapi_to_timeseries
from pipeline_service.pipelines.base import METADATA_SAVE_ERRORS, BasePipeline, ProgressCallback
from pipeline_service.pipelines.report import IngestionReport
from pipeline_service.util.documents import TransformedDocuments, dataframe_to_documents
from pipeline_service.util.time import generate_time_chunks
from space_weather_analysis.catalog.models import CatalogEntryDict, SourceType
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.clients.hapi import HapiClient
from space_weather_analysis.coverage.repository import CoverageRepository, MongoCoverageRepository
from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.models import MetadataSourceKind
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository
from space_weather_analysis.util.error_handling import DataProcessingError
from space_weather_analysis.util.hapi_parameters import MAX_ARRAY_COLUMNS, parameters_too_large_for_columns
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import ensure_aware, parse_iso_time

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FetchedInfo:
    """The /info response a server sent with the data of a chunk, the source of the dataset metadata.

    Attributes:
        info: The /info response as returned by hapiclient.
        server_url: The server that sent it.
        retrieved_at: When it was read.
    """

    info: dict[str, Any]
    server_url: str | None
    retrieved_at: datetime


class HapiPipeline(MongoLoader, BasePipeline):
    """Ingests HAPI datasets chunk by chunk into MongoDB time series collections.

    The HapiClient receives the HAPI sources of every dataset from the master catalog at the start of each run.
    """

    # Time span of one data request and of one check for already stored documents
    CHUNK_DURATION = timedelta(days=7)

    TIME_FIELD = "timestamp"

    # Keys hapiclient adds to the /info response to describe its own request, cache files and timing
    HAPICLIENT_REQUEST_KEYS = frozenset(
        {
            "x_server",
            "x_dataset",
            "x_parameters",
            "x_time.min",
            "x_time.max",
            "x_requestDate",
            "x_cacheDir",
            "x_downloadTime",
            "x_readTime",
            "x_metaFileParsed",
            "x_dataFileParsed",
            "x_metaFile",
            "x_dataFile",
            "x_totalTime",
        }
    )

    def __init__(self, db: Database) -> None:
        """Initializes the pipeline with its repositories.

        Args:
            db: The MongoDB database instance.
        """
        super().__init__(db)
        self.catalog_repository: CatalogRepository = MongoCatalogRepository(db)
        self.metadata_repository: MetadataRepository = MongoMetadataRepository(db)
        self.coverage_repository: CoverageRepository = MongoCoverageRepository(db)
        self.client = HapiClient()

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Ingests each dataset in chunks of CHUNK_DURATION, skipping chunks that already hold documents.

        Args:
            datasets: The IDs of the datasets to ingest.
            start_time: The start of the time range; None takes the earliest catalog start of the dataset.
            end_time: The end of the time range; None takes the latest catalog stop, or now for an ongoing source.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with the number of tried chunks and the chunks that failed.

        Raises:
            ValueError: If no dataset is given, the master catalog lists no HAPI source, or the time range of a dataset cannot be resolved.
        """
        if not datasets:
            raise ValueError("The HAPI pipeline needs at least one dataset.")
        self.client = HapiClient(self.catalog_repository.get_source_map(SourceType.HAPI))
        if not self.client.catalog_map:
            raise ValueError("The master catalog lists no dataset with a HAPI source.")

        report = IngestionReport()
        for dataset_index, dataset_id in enumerate(datasets):
            effective_start, effective_end = self._resolve_time_range(dataset_id, start_time, end_time)
            if effective_start is None or effective_end is None:
                raise ValueError(f"The time range of {dataset_id} cannot be resolved from the request or the master catalog.")

            time_chunks = generate_time_chunks(effective_start, effective_end, self.CHUNK_DURATION)
            logger.info("Dataset %s: ingesting %s to %s in %d chunks.", dataset_id, effective_start, effective_end, len(time_chunks))
            collection_name = sanitize_collection_name(dataset_id)
            self._ensure_timeseries_collection(collection_name, time_field=self.TIME_FIELD, meta_field="metadata")

            latest_info: FetchedInfo | None = None
            for chunk_index, (chunk_start, chunk_stop) in enumerate(time_chunks):
                self._report_chunk_progress(
                    progress_callback,
                    dataset_index,
                    len(datasets),
                    chunk_index,
                    len(time_chunks),
                    f"Dataset {dataset_id}: Processing chunk {chunk_index + 1}/{len(time_chunks)}",
                )
                if self.db[collection_name].find_one({self.TIME_FIELD: {"$gte": chunk_start, "$lt": chunk_stop}}, {"_id": 1}):
                    logger.info("Skipping chunk %s to %s of %s: documents already stored.", chunk_start, chunk_stop, dataset_id)
                    continue

                chunk_info = self._ingest_chunk(
                    report,
                    dataset_id,
                    chunk_start,
                    chunk_stop,
                    partial(self._process_dataset_chunk, dataset_id, collection_name, chunk_start, chunk_stop),
                )
                if chunk_info is not None:
                    latest_info = chunk_info

            if latest_info is not None:
                # Reported once per dataset, as every chunk skips the same parameters
                skipped_parameters = parameters_too_large_for_columns(latest_info.info)
                if skipped_parameters:
                    logger.warning(
                        "Dataset %s: arrays with more than %d elements are not stored: %s", dataset_id, MAX_ARRAY_COLUMNS, skipped_parameters
                    )
                self._save_metadata(dataset_id, collection_name, latest_info)
                self._refresh_coverage(self.coverage_repository, dataset_id, collection_name)

        return report

    def _resolve_time_range(self, dataset_id: str, start_time: datetime | None, end_time: datetime | None) -> tuple[datetime | None, datetime | None]:
        """Determines the time range of a dataset, taking missing bounds from its catalog sources.

        The range of an ongoing source ends now.

        Args:
            dataset_id: The dataset ID.
            start_time: The requested start, or None.
            end_time: The requested end, or None.

        Returns:
            Start and end in UTC, or (None, None) if they cannot be resolved.
        """
        if start_time and end_time:
            return ensure_aware(start_time), ensure_aware(end_time)

        entries = self.client.catalog_map.get(dataset_id)
        if not entries:
            logger.warning("No HAPI source of %s in the master catalog to resolve its time range.", dataset_id)
            return None, None

        global_start = min(self._parse_catalog_times(entries, "start_time"), default=None)
        end_times = self._parse_catalog_times(entries, "end_time")
        # An ongoing source has data up to now, beyond the stop time recorded in the catalog
        if any(entry.get("ongoing") for entry in entries):
            end_times.append(datetime.now(UTC))
        global_end = max(end_times, default=None)

        if global_start and global_end:
            logger.info("Resolved the time range of %s: %s to %s", dataset_id, global_start, global_end)
            return global_start, global_end
        return None, None

    @staticmethod
    def _parse_catalog_times(entries: list[CatalogEntryDict], time_key: Literal["start_time", "end_time"]) -> list[datetime]:
        """Parses one bound of the time range of each catalog entry.

        Args:
            entries: The catalog entries of a dataset.
            time_key: The key of the bound in the time range, 'start_time' or 'end_time'.

        Returns:
            The parsed times, without the entries whose bound is missing or unparsable.
        """
        parsed_times = []
        for entry in entries:
            time_string = entry.get("time_range", {}).get(time_key)
            parsed_time = parse_iso_time(time_string) if time_string else None
            if parsed_time:
                parsed_times.append(parsed_time)
        return parsed_times

    def _process_dataset_chunk(self, dataset_id: str, collection_name: str, chunk_start: datetime, chunk_stop: datetime) -> FetchedInfo | None:
        """Fetches one chunk and stores its rows as documents.

        Args:
            dataset_id: The dataset ID.
            collection_name: The collection holding the data documents.
            chunk_start: The start of the chunk (inclusive).
            chunk_stop: The end of the chunk (exclusive).

        Returns:
            The /info response the server sent with the data, or None if no source has data for the chunk.

        Raises:
            DataRetrievalError: If the request failed.
            DataProcessingError: If the response cannot be converted into a time series.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        result = self.client.fetch(dataset_id, chunk_start, chunk_stop)
        if result.data is None:
            return None
        data, info = result.data
        retrieved_at = datetime.now(UTC)

        time_series = convert_hapi_to_timeseries(data, info)
        if not time_series:
            raise DataProcessingError(f"The HAPI response of {dataset_id} for {chunk_start} to {chunk_stop} cannot be converted into a time series.")

        documents = self._transform_timeseries_to_docs(time_series, result.source_url or SourceType.HAPI)
        self._load_data(collection_name, documents)
        return FetchedInfo(info, result.source_url, retrieved_at)

    def _save_metadata(self, dataset_id: str, collection_name: str, fetched_info: FetchedInfo) -> None:
        """Saves the metadata record built from the /info response and the response itself.

        The raw response is saved without the keys hapiclient adds about its request.
        A failing save is logged, as the stored data documents stay valid without metadata.

        Args:
            dataset_id: The ID of the dataset.
            collection_name: The collection holding the data documents.
            fetched_info: The /info response, its server and when it was read.
        """
        server_info = {key: value for key, value in fetched_info.info.items() if key not in self.HAPICLIENT_REQUEST_KEYS}
        try:
            record = build_hapi_metadata(dataset_id, collection_name, server_info, fetched_info.server_url, fetched_info.retrieved_at)
            self.metadata_repository.save_raw(dataset_id, MetadataSourceKind.HAPI, server_info, fetched_info.server_url, fetched_info.retrieved_at)
            save_with_overlays(self.metadata_repository, [record])
        except METADATA_SAVE_ERRORS:
            logger.exception("Failed to save metadata of %s", dataset_id)
            return
        logger.info("Saved metadata of %s.", dataset_id)

    @staticmethod
    def _transform_timeseries_to_docs(time_series: GenericTimeSeries, source_info: str) -> TransformedDocuments:
        """Converts a time series into time series documents.

        Args:
            time_series: The time series of one chunk.
            source_info: The server the data come from.

        Returns:
            One document per row.
        """
        return dataframe_to_documents(time_series.to_dataframe(), source_info)
