import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Literal

from pymongo.database import Database

from pipeline_service.database.mongo_loader import MongoLoader
from pipeline_service.parsing.hapi import convert_hapi_to_frame
from pipeline_service.pipelines.base import METADATA_SAVE_ERRORS, BasePipeline, ProgressCallback
from pipeline_service.pipelines.report import IngestionReport
from pipeline_service.util.documents import TIME_FIELD, dataframe_to_documents
from pipeline_service.util.time import generate_time_chunks
from space_weather_analysis.catalog.models import CatalogEntryDict, CatalogMap, SourceType
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.clients.hapi import HapiClient
from space_weather_analysis.coverage.repository import CoverageRepository, MongoCoverageRepository
from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.models import MAX_ARRAY_COLUMNS, MetadataSourceKind
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository
from space_weather_analysis.util.hapi_parameters import parameters_too_large_for_columns
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import ensure_aware, parse_iso_time

logger = logging.getLogger(__name__)

# Time span of one data request and of one check for already stored documents.
DEFAULT_CHUNK_DURATION = timedelta(days=7)


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

    The client receives the HAPI sources of every dataset from the master catalog at the start of each run.
    """

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

    def __init__(
        self,
        db: Database,
        chunk_duration: timedelta = DEFAULT_CHUNK_DURATION,
        client_factory: Callable[[CatalogMap], HapiClient] = HapiClient,
    ) -> None:
        """Initializes the pipeline with its repositories.

        Args:
            db: The MongoDB database instance.
            chunk_duration: The time span of one data request.
            client_factory: Builds the client from the HAPI sources of the master catalog at the start of each run.
        """
        super().__init__(db)
        self.chunk_duration = chunk_duration
        self.client_factory = client_factory
        self.catalog_repository: CatalogRepository = MongoCatalogRepository(db)
        self.metadata_repository: MetadataRepository = MongoMetadataRepository(db)
        self.coverage_repository: CoverageRepository = MongoCoverageRepository(db)

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Ingests each dataset in chunks of the chunk duration, skipping chunks that already hold documents.

        The time range of every dataset is resolved before the first chunk, so an invalid request stores nothing.

        Args:
            datasets: The IDs of the datasets to ingest.
            start_time: The start of the time range; None takes the earliest catalog start of the dataset.
            end_time: The end of the time range; None takes the latest catalog stop, or now for an ongoing source.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with the number of tried chunks and the chunks that failed.

        Raises:
            ValueError: If no dataset is given, a dataset has no HAPI source in the master catalog, or its time range cannot be resolved.
        """
        if not datasets:
            raise ValueError("The HAPI pipeline needs at least one dataset.")
        client = self.client_factory(self.catalog_repository.get_source_map(SourceType.HAPI))
        time_ranges = {dataset_id: self._resolve_time_range(client, dataset_id, start_time, end_time) for dataset_id in datasets}

        report = IngestionReport()
        for dataset_index, dataset_id in enumerate(datasets):
            effective_start, effective_end = time_ranges[dataset_id]
            time_chunks = generate_time_chunks(effective_start, effective_end, self.chunk_duration)
            logger.info("Dataset %s: ingesting %s to %s in %d chunks.", dataset_id, effective_start, effective_end, len(time_chunks))
            collection_name = sanitize_collection_name(dataset_id)
            self._ensure_timeseries_collection(collection_name)

            latest_info: FetchedInfo | None = None
            for chunk_index, (chunk_start, chunk_stop) in enumerate(time_chunks):
                self._report_chunk_progress(
                    progress_callback, dataset_id, dataset_position=(dataset_index, len(datasets)), chunk_position=(chunk_index, len(time_chunks))
                )
                if self.db[collection_name].find_one({TIME_FIELD: {"$gte": chunk_start, "$lt": chunk_stop}}, {"_id": 1}):
                    logger.info("Skipping chunk %s to %s of %s: documents already stored.", chunk_start, chunk_stop, dataset_id)
                    continue

                chunk_info = self._ingest_chunk(
                    report,
                    dataset_id,
                    chunk_start,
                    chunk_stop,
                    partial(self._process_dataset_chunk, client, dataset_id, collection_name, chunk_start, chunk_stop),
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

    def _resolve_time_range(
        self, client: HapiClient, dataset_id: str, start_time: datetime | None, end_time: datetime | None
    ) -> tuple[datetime, datetime]:
        """Determines the time range of a dataset, taking each missing bound from its catalog sources.

        The range of an ongoing source ends now.

        Args:
            client: The client holding the HAPI sources of the master catalog.
            dataset_id: The dataset ID.
            start_time: The requested start, or None.
            end_time: The requested end, or None.

        Returns:
            Start and end in UTC.

        Raises:
            ValueError: If the master catalog lists no HAPI source for the dataset, or a missing bound cannot be taken from it.
        """
        entries = client.catalog_map.get(dataset_id)
        if not entries:
            raise ValueError(f"The master catalog lists no HAPI source for dataset '{dataset_id}'.")

        if start_time is not None:
            start = ensure_aware(start_time)
        else:
            catalog_start = min(self._parse_catalog_times(entries, "start_time"), default=None)
            if catalog_start is None:
                raise ValueError(f"The start of {dataset_id} is neither requested nor given by its catalog sources.")
            start = catalog_start

        if end_time is not None:
            end = ensure_aware(end_time)
        else:
            end_times = self._parse_catalog_times(entries, "end_time")
            # An ongoing source has data up to now, beyond the stop time recorded in the catalog
            if any(entry.get("ongoing") for entry in entries):
                end_times.append(datetime.now(UTC))
            catalog_end = max(end_times, default=None)
            if catalog_end is None:
                raise ValueError(f"The end of {dataset_id} is neither requested nor given by its catalog sources.")
            end = catalog_end
        return start, end

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

    def _process_dataset_chunk(
        self, client: HapiClient, dataset_id: str, collection_name: str, chunk_start: datetime, chunk_stop: datetime
    ) -> FetchedInfo | None:
        """Fetches one chunk and stores its rows as documents.

        Args:
            client: The client fetching the data.
            dataset_id: The dataset ID.
            collection_name: The collection holding the data documents.
            chunk_start: The start of the chunk (inclusive).
            chunk_stop: The end of the chunk (exclusive).

        Returns:
            The /info response the server sent with the data, or None if no source has data for the chunk.

        Raises:
            DataRetrievalError: If the request failed.
            DataProcessingError: If the response cannot be converted into columns.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        result = client.fetch(dataset_id, chunk_start, chunk_stop)
        if result.data is None:
            return None
        data, info = result.data
        retrieved_at = datetime.now(UTC)

        frame = convert_hapi_to_frame(data, info)
        self._load_data(collection_name, dataframe_to_documents(frame, result.source_url or SourceType.HAPI))
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
