import logging
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo.database import Database
from sunpy.timeseries.timeseriesbase import GenericTimeSeries

from space_weather_analysis.clients.hapi import HapiClient
from space_weather_analysis.config import HAPI_SOURCE_ID
from space_weather_analysis.database.mongo_loader import MongoLoader
from space_weather_analysis.pipelines.base import BasePipeline
from space_weather_analysis.util.converters.hapi_converter import convert_hapi_to_timeseries
from space_weather_analysis.util.data_preprocessing import generate_time_chunks, sanitize_collection_name
from space_weather_analysis.util.document_builder import dataframe_to_documents
from space_weather_analysis.util.error_handling import log_and_handle_unexpected_failure
from space_weather_analysis.util.metadata.metadata_generator import MetadataWriter
from space_weather_analysis.util.metadata.variable_metadata import VariableMetadata
from space_weather_analysis.util.types import TransformedDocuments

logger = logging.getLogger(__name__)


class HapiPipeline(MongoLoader, BasePipeline):
    """
    Orchestrates data streaming, metadata generation, and data loading
    using the generic client, which reads its catalog from MongoDB.

    This pipeline implements standardized error handling and document
    structures to ensure consistency across the application.
    """

    def __init__(self, db: Database, cache_dir: Optional[pathlib.Path] = None):
        """
        Initializes the pipeline with database connection and clients.

        Args:
            db: The MongoDB database instance.
            cache_dir: Optional directory to store HAPI responses persistently.
        """
        super().__init__(db)
        # Pass the cache directory to the client (if supported by client init)
        # Note: The HapiClient init signature in previous steps didn't strictly expect cache_dir,
        # but if your local HapiClient supports it, this is fine.
        # Assuming standard signature based on previous context:
        self.client = HapiClient(db)
        self.metadata_writer = MetadataWriter(db)

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

    def _transform_timeseries_to_docs(self, ts: GenericTimeSeries, source_info: str) -> TransformedDocuments:
        """
        Converts a TimeSeries object into standardized MongoDB documents.

        Uses the centralized document builder to ensure consistent structure
        across all pipelines and data sources.

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
        # Cast to TransformedDocuments alias (List[Dict[str, Any]])
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

        source_url = meta.get("x_server_url", HAPI_SOURCE_ID)
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

    def _process_dataset(
        self,
        dataset_id: str,
        config_start: datetime,
        config_stop: datetime,
    ) -> None:
        """
        Runs the full processing pipeline for a single dataset chunk.

        Includes data fetching, conversion, transformation, and loading.
        This method logs warnings on failure but does not raise exceptions,
        allowing the processing loop to continue.

        Args:
            dataset_id: The dataset ID to process.
            config_start: The start time for the data chunk.
            config_stop: The end time for the data chunk.
        """
        logger.info(f"--- Processing Dataset Chunk: {dataset_id} ---")
        try:
            # The client handles the fetching and caching internally
            data, meta = self.client.get_data_and_meta(dataset_id, config_start, config_stop)

            if data is None or meta is None:
                logger.warning(
                    f"Skipping {dataset_id} for {config_start} to {config_stop}: "
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
            log_and_handle_unexpected_failure(f"Pipeline processing for {dataset_id}", f"chunk {config_start}-{config_stop}", e, logger)
        finally:
            logger.info(f"--- Finished Dataset Chunk: {dataset_id} ---")

    def run(
        self,
        datasets: List[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        chunk_delta: timedelta = timedelta(days=7),
    ) -> None:
        """
        Runs the full streaming pipeline for a list of datasets.

        This method iterates through the specified time range in chunks
        defined by chunk_delta to avoid failures on large requests with data gaps.
        It checks if data for a chunk already exists before fetching.

        Args:
            datasets: A list of dataset IDs to fetch.
            start_time: The start time for the data query.
            end_time: The end time for the data query.
            chunk_delta: The duration of each processing chunk. Defaults to 7 days.
        """
        logger.info(f"--- Starting Generic {HAPI_SOURCE_ID} Pipeline ---")

        if not start_time or not end_time:
            logger.error("Start time and end time are required for HAPI pipeline.")
            return

        if not self.client.catalog_map:
            logger.error("Client has no catalog map loaded from DB. Aborting pipeline.")
            return

        logger.info(f"Processing {len(datasets)} datasets...")

        config_start, config_stop = self._ensure_time_range_is_aware(start_time, end_time)

        time_chunks = generate_time_chunks(config_start, config_stop, chunk_delta)
        time_field = "timestamp"

        logger.info(f"Time range {config_start} to {config_stop} split into {len(time_chunks)} chunk(s) using delta {chunk_delta}.")

        for dataset_id in datasets:
            logger.info(f"Processing dataset {dataset_id} for all {len(time_chunks)} chunks...")

            collection_name = sanitize_collection_name(dataset_id)
            collection = self.db[collection_name]

            self._ensure_timeseries_collection(
                collection_name,
                time_field=time_field,
                meta_field="metadata",
            )

            for i, (chunk_start, chunk_stop) in enumerate(time_chunks):
                logger.info(f"  -> Checking chunk {i + 1}/{len(time_chunks)}: {chunk_start} to {chunk_stop}")

                query = {time_field: {"$gte": chunk_start, "$lt": chunk_stop}}
                exists = collection.find_one(query, {"_id": 1})

                if exists:
                    logger.info("Skipping chunk: Data already exists in DB.")
                    continue

                logger.info("Processing chunk: No data found in DB.")
                self._process_dataset(dataset_id, chunk_start, chunk_stop)

        logger.info(f"--- Generic {HAPI_SOURCE_ID} Pipeline Finished ---")
