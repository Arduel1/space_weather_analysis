import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from pymongo.database import Database
from sunpy.timeseries import GenericTimeSeries

from space_weather_analysis.clients.hapi import HapiClient
from space_weather_analysis.util.error_handling import log_and_handle_unexpected_failure
from space_weather_analysis.util.time_utils import parse_iso_time
from pipeline_service.database.mongo_loader import MongoLoader
from pipeline_service.metadata.generator import MetadataWriter
from pipeline_service.metadata.models import VariableMetadata
from pipeline_service.parsing.hapi import convert_hapi_to_timeseries
from pipeline_service.pipelines.base import BasePipeline
from pipeline_service.util.documents import dataframe_to_documents
from pipeline_service.util.naming import sanitize_collection_name
from pipeline_service.util.time import generate_time_chunks

logger = logging.getLogger(__name__)


class HapiPipeline(MongoLoader, BasePipeline):
    """
    Orchestrates data streaming, metadata generation, and data loading
    using the generic HAPI client.

    This pipeline handles the complete workflow from fetching data via the HAPI
    protocol to storing it in MongoDB time-series collections. It leverages the
    common HapiClient which maintains a catalog of available servers and datasets.
    """

    # Identifying string for source tracking in metadata
    SOURCE_ID = "HAPI"

    def __init__(self, db: Database, *args: Any, **kwargs: Any) -> None:
        """
        Initializes the pipeline with database connection and clients.

        Args:
            db: The MongoDB database instance.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        super().__init__(db)
        self.client = HapiClient(db)
        self.metadata_writer = MetadataWriter(db)

    def run(
        self,
        datasets: List[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        chunk_delta: timedelta = timedelta(days=7),
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> None:
        """
        Runs the full streaming pipeline for a list of datasets.

        This method iterates through the specified time range in chunks
        to avoid failures on large requests. It updates the progress via
        the callback if provided.

        Args:
            datasets: A list of dataset IDs to fetch.
            start_time: The start time for the data query (or None for auto).
            end_time: The end time for the data query (or None for auto).
            chunk_delta: The duration of each processing chunk. Defaults to 7 days.
            progress_callback: Optional callback to report progress (0.0 to 1.0) and status message.
        """
        logger.info(f"Starting Generic {self.SOURCE_ID} Pipeline")

        if not self.client.catalog_map:
            logger.error("Client has no catalog map loaded from DB. Aborting pipeline.")
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

            for j, (chunk_start, chunk_stop) in enumerate(time_chunks):
                # Calculate progress: Base progress for completed datasets + progress within current dataset
                if progress_callback:
                    dataset_base_progress = i / total_datasets
                    chunk_progress = j / total_chunks
                    # Scale chunk progress to fit into the dataset's slice of total progress
                    current_progress = dataset_base_progress + (chunk_progress / total_datasets)

                    msg = f"Dataset {dataset_id}: Processing chunk {j + 1}/{total_chunks}"
                    progress_callback(current_progress, msg)

                logger.info(f"Checking chunk {j + 1}/{total_chunks}: {chunk_start} to {chunk_stop}")

                query = {time_field: {"$gte": chunk_start, "$lt": chunk_stop}}
                exists = collection.find_one(query, {"_id": 1})

                if exists:
                    logger.info("Skipping chunk: Data already exists in DB.")
                    continue

                logger.info("Processing chunk: No data found in DB.")
                self._process_dataset_chunk(dataset_id, chunk_start, chunk_stop)

        logger.info(f"Generic {self.SOURCE_ID} Pipeline Finished")

    def _resolve_time_range(
        self,
        dataset_id: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        Determines the effective time range for a dataset.

        If start/end are None, it fetches the full range from the catalog/server.

        Args:
            dataset_id: The dataset identifier.
            start_time: Requested start time (can be None).
            end_time: Requested end time (can be None).

        Returns:
            A tuple of (start_time, end_time) in UTC, or (None, None) if unresolved.
        """
        if start_time and end_time:
            return self._ensure_time_range_is_aware(start_time, end_time)

        logger.info(f"Auto-detecting time range for {dataset_id}...")

        # Access catalog map from common client
        entries = self.client.catalog_map.get(dataset_id)
        if not entries:
            logger.warning(f"No catalog entries found for {dataset_id}. Cannot auto-detect range.")
            return None, None

        # Find the overall min start and max end from all available servers
        global_start: Optional[datetime] = None
        global_end: Optional[datetime] = None

        for entry in entries:
            tr = entry.get("time_range", {})
            s_str = tr.get("start_time")
            e_str = tr.get("end_time")

            if s_str:
                s_dt = parse_iso_time(s_str)
                if s_dt:
                    if global_start is None or s_dt < global_start:
                        global_start = s_dt

            if e_str:
                e_dt = parse_iso_time(e_str)
                if e_dt:
                    if global_end is None or e_dt > global_end:
                        global_end = e_dt

        if global_start and global_end:
            logger.info(f"Detected full range: {global_start} to {global_end}")
            return global_start, global_end

        return None, None

    def _process_dataset_chunk(
        self,
        dataset_id: str,
        chunk_start: datetime,
        chunk_stop: datetime,
    ) -> None:
        """
        Runs the full pipeline for a single dataset chunk.

        Includes data fetching, conversion, transformation, and loading.
        This method logs warnings on failure but does not raise exceptions,
        allowing the processing loop to continue.

        Args:
            dataset_id: The dataset ID to process.
            chunk_start: The start time for the data chunk.
            chunk_stop: The end time for the data chunk.
        """
        logger.info(f"Processing Dataset Chunk: {dataset_id}")
        try:
            # The client handles the fetching and caching internally
            data, meta = self.client.get_data(dataset_id, chunk_start, chunk_stop)

            if data is None or meta is None:
                logger.warning(
                    f"Skipping {dataset_id} for {chunk_start} to {chunk_stop}: "
                    "Could not retrieve data. All fallbacks failed or no time overlap found."
                )
                return

            ts = convert_hapi_to_timeseries(data, meta)

            if ts:
                self._load_data_to_db(ts, dataset_id, meta)
                self._save_metadata_to_db(meta, dataset_id)
            else:
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

    def _load_data_to_db(
        self,
        ts: GenericTimeSeries,
        dataset_id: str,
        meta: Dict[str, Any],
    ) -> None:
        """
        Internal helper to transform and load timeseries data into MongoDB.

        Args:
            ts: The 1D TimeSeries data.
            dataset_id: The ID of the dataset.
            meta: The metadata dictionary.
        """
        logger.info(f"Successfully converted {dataset_id} data to TimeSeries with {len(ts.to_table())} rows.")

        source_url = meta.get("x_server_url", self.SOURCE_ID)
        documents = self._transform_timeseries_to_docs(ts, source_info=source_url)

        collection_name = sanitize_collection_name(dataset_id)

        self._ensure_timeseries_collection(collection_name, time_field="timestamp", meta_field="metadata")
        self._load_data(collection_name, documents)

    def _save_metadata_to_db(self, meta: Dict[str, Any], dataset_id: str) -> None:
        """
        Internal helper to normalize and save metadata to MongoDB.

        Args:
            meta: The raw metadata dict from the client.
            dataset_id: The ID of the dataset.
        """
        variables = self._normalize_hapi_meta_to_variables(meta)
        source_info = {
            "Source HAPI Server": meta.get("x_server_url", "N/A"),
            "Data Start Date": meta.get("startDate", "N/A"),
            "Data Stop Date": meta.get("stopDate", "N/A"),
            "Description": meta.get("description", "N/A"),
            "Citation": meta.get("datasetCitation", "N/A"),
        }
        self.metadata_writer.save_metadata_to_db(
            dataset_id=dataset_id,
            variables=variables,
            source_info=source_info,
        )

    def _normalize_hapi_meta_to_variables(self, hapi_meta: Dict[str, Any]) -> List[VariableMetadata]:
        """
        Converts the raw metadata dictionary into a list of
        standardized VariableMetadata objects.

        Args:
            hapi_meta: The raw metadata dict from the client.

        Returns:
            A list of standardized metadata objects.
        """
        variables = []
        for param in hapi_meta.get("parameters", []):
            variables.append(
                VariableMetadata(
                    name=param.get("name"),
                    description=param.get("description"),
                    units=param.get("units"),
                    fill=param.get("fill"),
                    type=param.get("type"),
                    size=param.get("size"),
                )
            )
        return variables

    def _transform_timeseries_to_docs(self, ts: GenericTimeSeries, source_info: str) -> List[Dict[str, Any]]:
        """
        Converts a TimeSeries object into standardized MongoDB documents.

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

    def _ensure_time_range_is_aware(self, start_time: datetime, end_time: datetime) -> Tuple[datetime, datetime]:
        """
        Ensures both start and end times are timezone-aware in UTC.

        Args:
            start_time: The start time to validate.
            end_time: The end time to validate.

        Returns:
            A tuple of timezone-aware datetime objects.
        """
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        return start_time, end_time
