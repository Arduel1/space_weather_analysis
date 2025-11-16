import logging
from datetime import datetime, timezone
from typing import Any, Dict, Hashable, List, Tuple

import numpy as np
import pandas as pd
from pymongo.database import Database
from sunpy.timeseries.timeseriesbase import GenericTimeSeries

from space_weather_analysis.database.mongo_loader_base import MongoLoaderBase
from space_weather_analysis.hapi_pipeline.hapi_client import GenericHapiClient
from space_weather_analysis.util.converters.hapi_converter import convert_hapi_to_timeseries
from space_weather_analysis.util.data_preprocessing import sanitize_collection_name
from space_weather_analysis.util.metadata.metadata_generator import MetadataWriter
from space_weather_analysis.util.metadata.variable_metadata import VariableMetadata

logger = logging.getLogger(__name__)


class GenericHapiPipeline(MongoLoaderBase):
    """
    Orchestrates data streaming, metadata generation, and data loading
    using the generic client, which reads its catalog from MongoDB.
    """

    def __init__(
        self,
        db: Database,
    ):
        """
        Initializes the pipeline.

        Args:
            db (Database): The MongoDB database instance.
        """
        super().__init__(db)
        self.client = GenericHapiClient(db)
        self.metadata_writer = MetadataWriter(db)

    def _normalize_hapi_meta_to_variables(self, hapi_meta: Dict[str, Any]) -> List[VariableMetadata]:
        """
        Converts the raw metadata dictionary into a list of
        standardized VariableMetadata objects.

        Args:
            hapi_meta (Dict[str, Any]): The raw metadata dict from the client.

        Returns:
            List[VariableMetadata]: A list of standardized metadata objects.
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
        Converts a TimeSeries object into a list of MongoDB documents.

        Args:
            ts (GenericTimeSeries): The 1D TimeSeries data.
            source_info (str): A string identifying the data source.

        Returns:
            List[Dict[str, Any]]: A list of formatted MongoDB documents.
        """
        df = ts.to_dataframe()
        logger.info(f"Transforming {len(df)} TimeSeries rows to MongoDB documents...")

        data_payloads: List[Dict[Hashable, Any]] = df.to_dict("records")
        timestamps: np.ndarray = df.index.to_pydatetime()
        metadata = {"source": source_info}

        documents = [
            {
                "timestamp": ts_py,
                "metadata": metadata,
                # Ensure keys are strings for MongoDB.
                "data": {str(k): v for k, v in data.items() if not pd.isna(v)},
            }
            for ts_py, data in zip(timestamps, data_payloads)
        ]

        logger.info(f"Transformed {len(documents)} documents.")
        return documents

    def _ensure_time_range_is_aware(self, start_time: datetime, stop_time: datetime) -> Tuple[datetime, datetime]:
        """
        Ensures both start and stop times are timezone-aware (UTC).
        """
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if stop_time.tzinfo is None:
            stop_time = stop_time.replace(tzinfo=timezone.utc)
        return start_time, stop_time

    def _load_data_to_db(
        self,
        ts: GenericTimeSeries,
        dataset_id: str,
        meta: Dict[str, Any],
    ) -> None:
        """
        Internal helper to transform and load timeseries data into MongoDB.
        """
        logger.info(f"Successfully converted {dataset_id} data to TimeSeries with {len(ts.to_table())} rows.")

        source_url = meta.get("x_server_url", "HAPI")
        documents = self._transform_timeseries_to_docs(ts, source_info=source_url)

        collection_name = sanitize_collection_name(dataset_id)

        self._ensure_timeseries_collection(collection_name, time_field="timestamp", meta_field="metadata")
        self._load_data(collection_name, documents)

    def _save_metadata_to_db(self, meta: Dict[str, Any], dataset_id: str) -> None:
        """
        Internal helper to normalize and save metadata to MongoDB.
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
        Runs the full processing pipeline for a single dataset.
        Includes data fetching, conversion, transformation, and loading.
        """
        logger.info(f"--- Processing Dataset: {dataset_id} ---")
        try:
            data, meta = self.client.get_data_and_meta(dataset_id, config_start, config_stop)

            if data is None or meta is None:
                logger.warning(
                    f"Skipping {dataset_id}: Could not retrieve data. All fallbacks failed or no time overlap found."
                )
                return

            ts = convert_hapi_to_timeseries(data, meta)

            if ts:
                self._load_data_to_db(ts, dataset_id, meta)
            else:
                logger.warning(f"Failed to convert {dataset_id} to TimeSeries. Cannot load to DB.")

            self._save_metadata_to_db(meta, dataset_id)

        except Exception as e:
            logger.error(
                f"Pipeline failed unexpectedly for {dataset_id}: {e}",
                exc_info=True,
            )
        finally:
            logger.info(f"--- Finished Dataset: {dataset_id} ---")

    def run(
        self,
        datasets_to_process: List[str],
        start_time: datetime,
        stop_time: datetime,
    ) -> None:
        """
        Runs the full streaming pipeline for a list of datasets.

        Args:
            datasets_to_process (List[str]): A list of dataset IDs to fetch.
            start_time (datetime): The start time for the data query.
            stop_time (datetime): The end time for the data query.
        """
        logger.info("--- Starting Generic HAPI Pipeline ---")
        if not self.client.catalog_map:
            logger.error("Client has no catalog map loaded from DB. Aborting pipeline.")
            return

        logger.info(f"Processing {len(datasets_to_process)} datasets...")

        config_start, config_stop = self._ensure_time_range_is_aware(start_time, stop_time)

        for dataset_id in datasets_to_process:
            self._process_dataset(dataset_id, config_start, config_stop)

        logger.info("--- Generic HAPI Pipeline Finished ---")
