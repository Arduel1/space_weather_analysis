import logging
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from pymongo.database import Database
from sunpy.timeseries import GenericTimeSeries

from pipeline_service.database.mongo_loader import MongoLoader
from pipeline_service.parsing.hapi import convert_hapi_to_timeseries
from pipeline_service.pipelines.base import BasePipeline, ProgressCallback
from pipeline_service.util.documents import TransformedDocuments, dataframe_to_documents
from pipeline_service.util.time import generate_time_chunks
from space_weather_analysis.catalog.models import CatalogEntryDict
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.clients.hapi import HapiClient
from space_weather_analysis.coverage.repository import CoverageRepository, MongoCoverageRepository
from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository
from space_weather_analysis.util.error_handling import log_and_handle_unexpected_failure
from space_weather_analysis.util.hapi_parameters import MAX_ARRAY_COLUMNS, parameters_too_large_for_columns
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import ensure_aware, parse_iso_time

logger = logging.getLogger(__name__)


class HapiPipeline(MongoLoader, BasePipeline):
    """Orchestrates data streaming, metadata generation and loading via the generic HAPI client.

    This pipeline handles the complete workflow from fetching data via the HAPI
    protocol to storing it in MongoDB time-series collections. It leverages the
    common HapiClient, which receives the HAPI sources of every dataset from the master catalog.
    """

    # Identifying string for source tracking in metadata
    SOURCE_ID = "HAPI"

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

    def __init__(self, db: Database, base_data_path: pathlib.Path) -> None:
        """Initializes the pipeline with database connection and clients.

        Args:
            db: The MongoDB database instance.
            base_data_path: Root directory for data files, accepted like in every pipeline but unused,
                as HAPI data goes directly into MongoDB without files.
        """
        super().__init__(db)
        catalog_repository: CatalogRepository = MongoCatalogRepository(db)
        hapi_sources = catalog_repository.get_source_map(self.SOURCE_ID)
        logger.info(f"Loaded the HAPI sources of {len(hapi_sources)} datasets from the master catalog.")
        self.client = HapiClient(hapi_sources)
        self.metadata_repository: MetadataRepository = MongoMetadataRepository(db)
        self.coverage_repository: CoverageRepository = MongoCoverageRepository(db)

    def run(
        self,
        datasets: List[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        progress_callback: Optional[ProgressCallback] = None,
        chunk_delta: timedelta = timedelta(days=7),
    ) -> None:
        """Runs the full streaming pipeline for a list of datasets.

        This method iterates through the specified time range in chunks
        to avoid failures on large requests. It updates the progress via
        the callback if provided.

        Args:
            datasets: A list of dataset IDs to fetch.
            start_time: The start time for the data query (or None for auto).
            end_time: The end time for the data query (or None for auto).
            progress_callback: Optional callback to report progress (0.0 to 1.0) and status message.
            chunk_delta: The duration of each processing chunk. Defaults to 7 days.
        """
        logger.info(f"Starting Generic {self.SOURCE_ID} Pipeline")

        if not self.client.catalog_map:
            logger.error("The master catalog lists no dataset with a HAPI source. Aborting pipeline.")
            return

        total_datasets = len(datasets)
        logger.info(f"Processing {total_datasets} datasets...")

        for i, dataset_id in enumerate(datasets):
            # Resolve Time Range Per Dataset
            effective_start, effective_end = self._resolve_time_range(dataset_id, start_time, end_time)

            if not effective_start or not effective_end:
                logger.error(f"Could not determine valid time range for {dataset_id}. Skipping.")
                continue

            # Generate Chunks
            time_chunks = generate_time_chunks(effective_start, effective_end, chunk_delta)
            total_chunks = len(time_chunks)
            time_field = "timestamp"

            logger.info(f"Dataset {dataset_id}: Processing range {effective_start} to {effective_end} ({total_chunks} chunks).")

            collection_name = sanitize_collection_name(dataset_id)
            collection = self.db[collection_name]

            self._ensure_timeseries_collection(
                collection_name,
                time_field=time_field,
                meta_field="metadata",
            )

            # Info of the last chunk with data and when it was read, the source of the dataset metadata
            latest_info: Optional[Dict[str, Any]] = None
            latest_retrieved_at: Optional[datetime] = None

            for j, (chunk_start, chunk_stop) in enumerate(time_chunks):
                self._report_chunk_progress(
                    progress_callback, i, total_datasets, j, total_chunks, f"Dataset {dataset_id}: Processing chunk {j + 1}/{total_chunks}"
                )

                logger.info(f"Checking chunk {j + 1}/{total_chunks}: {chunk_start} to {chunk_stop}")

                query = {time_field: {"$gte": chunk_start, "$lt": chunk_stop}}
                exists = collection.find_one(query, {"_id": 1})

                if exists:
                    logger.info("Skipping chunk: Data already exists in DB.")
                    continue

                logger.info("Processing chunk: No data found in DB.")
                chunk_info = self._process_dataset_chunk(dataset_id, chunk_start, chunk_stop)
                if chunk_info is not None:
                    latest_info = chunk_info
                    latest_retrieved_at = datetime.now(timezone.utc)

            if latest_info is not None and latest_retrieved_at is not None:
                # Reported once per dataset, as every chunk skips the same parameters
                skipped_parameters = parameters_too_large_for_columns(latest_info)
                if skipped_parameters:
                    logger.warning(f"Dataset {dataset_id}: arrays with more than {MAX_ARRAY_COLUMNS} elements are not stored: {skipped_parameters}")
                self._save_metadata(dataset_id, collection_name, latest_info, latest_retrieved_at)
                self._refresh_coverage(self.coverage_repository, dataset_id, collection_name)

        logger.info(f"Generic {self.SOURCE_ID} Pipeline Finished")

    def _resolve_time_range(
        self,
        dataset_id: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Determines the effective time range for a dataset.

        If start/end are None, it fetches the full range from the catalog/server; the range of
        an ongoing source ends now.

        Args:
            dataset_id: The dataset identifier.
            start_time: Requested start time (can be None).
            end_time: Requested end time (can be None).

        Returns:
            A tuple of (start_time, end_time) in UTC, or (None, None) if unresolved.
        """
        if start_time and end_time:
            return ensure_aware(start_time), ensure_aware(end_time)

        logger.info(f"Auto-detecting time range for {dataset_id}...")

        # The HAPI sources the client received from the master catalog
        entries = self.client.catalog_map.get(dataset_id)
        if not entries:
            logger.warning(f"No catalog entries found for {dataset_id}. Cannot auto-detect range.")
            return None, None

        # Find the overall min start and max end from all available servers
        global_start = min(self._parse_catalog_times(entries, "start_time"), default=None)
        end_times = self._parse_catalog_times(entries, "end_time")
        # An ongoing source has data up to now, beyond the stop time recorded in the catalog
        if any(entry.get("ongoing") for entry in entries):
            end_times.append(datetime.now(timezone.utc))
        global_end = max(end_times, default=None)

        if global_start and global_end:
            logger.info(f"Detected full range: {global_start} to {global_end}")
            return global_start, global_end

        return None, None

    @staticmethod
    def _parse_catalog_times(entries: List[CatalogEntryDict], time_key: Literal["start_time", "end_time"]) -> List[datetime]:
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
        self,
        dataset_id: str,
        chunk_start: datetime,
        chunk_stop: datetime,
    ) -> Optional[Dict[str, Any]]:
        """Runs the full pipeline for a single dataset chunk.

        Includes data fetching, conversion, transformation, and loading.
        This method logs warnings on failure but does not raise exceptions,
        allowing the processing loop to continue.

        Args:
            dataset_id: The dataset ID to process.
            chunk_start: The start time for the data chunk.
            chunk_stop: The end time for the data chunk.

        Returns:
            The /info response the server sent with the data, or None if no documents were stored.
        """
        logger.info(f"Processing Dataset Chunk: {dataset_id}")
        try:
            # The client handles the fetching and caching internally
            data, meta = self.client.get_data(dataset_id, chunk_start, chunk_stop)

            if data is None or meta is None:
                logger.info(
                    f"Skipping {dataset_id} for {chunk_start} to {chunk_stop}: "
                    "Could not retrieve data. All fallbacks failed or no time overlap found."
                )
                return None

            ts = convert_hapi_to_timeseries(data, meta)

            if ts:
                self._load_data_to_db(ts, dataset_id, meta)
                return meta

            logger.warning(f"Failed to convert {dataset_id} to TimeSeries. Cannot load to DB.")

        except Exception as e:
            log_and_handle_unexpected_failure(
                f"Pipeline processing for {dataset_id}",
                f"chunk {chunk_start}-{chunk_stop}",
                e,
                logger,
            )
        finally:
            logger.info(f"Finished Dataset Chunk: {dataset_id}")

        return None

    def _load_data_to_db(
        self,
        ts: GenericTimeSeries,
        dataset_id: str,
        meta: Dict[str, Any],
    ) -> None:
        """Internal helper to transform and load timeseries data into MongoDB.

        Args:
            ts: The 1D TimeSeries data.
            dataset_id: The ID of the dataset.
            meta: The metadata dictionary.
        """
        logger.info(f"Successfully converted {dataset_id} data to TimeSeries with {len(ts.to_table())} rows.")

        source_url = meta.get("x_server_url", self.SOURCE_ID)
        documents = self._transform_timeseries_to_docs(ts, source_info=source_url)

        collection_name = sanitize_collection_name(dataset_id)
        self._load_data(collection_name, documents)

    def _save_metadata(self, dataset_id: str, collection_name: str, info: Dict[str, Any], retrieved_at: datetime) -> None:
        """Saves the metadata record built from the /info response and the response itself.

        The raw response is saved without the keys hapiclient adds about its request.
        A failing save is logged, as the stored data documents stay valid without metadata.

        Args:
            dataset_id: The ID of the dataset.
            collection_name: The collection holding the data documents.
            info: The /info response as returned by the client, with the server URL in 'x_server_url'.
            retrieved_at: When the response was read.
        """
        server_url = info.get("x_server_url")
        server_info = {key: value for key, value in info.items() if key not in self.HAPICLIENT_REQUEST_KEYS}
        try:
            record = build_hapi_metadata(dataset_id, collection_name, server_info, server_url, retrieved_at)
            self.metadata_repository.save_raw(dataset_id, "hapi", server_info, server_url, retrieved_at)
            self.metadata_repository.save(record)
            logger.info(f"Saved metadata of {dataset_id}.")
        except Exception as e:
            logger.error(f"Failed to save metadata of {dataset_id}: {e}", exc_info=True)

    def _transform_timeseries_to_docs(self, ts: GenericTimeSeries, source_info: str) -> TransformedDocuments:
        """Converts a TimeSeries object into standardized MongoDB documents.

        Args:
            ts: The 1D TimeSeries data.
            source_info: A string identifying the data source.

        Returns:
            A list of formatted MongoDB documents.
        """
        df = ts.to_dataframe()
        logger.info(f"Transforming {len(df)} TimeSeries rows to MongoDB documents...")

        documents = dataframe_to_documents(df, source_info)

        logger.info(f"Transformed {len(documents)} documents.")
        return documents
