import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, List

import pandas as pd
import sunpy.timeseries
from pymongo.database import Database

from space_weather_analysis.database.mongo_loader_base import MongoLoaderBase
from space_weather_analysis.hapi_pipeline.hapi_client import GenericHapiClient
from space_weather_analysis.hapi_pipeline.hapi_converter import convert_hapi_to_timeseries
from space_weather_analysis.util.metadata.metadata_generator import MetadataWriter
from space_weather_analysis.util.metadata.variable_metadata import VariableMetadata

logger = logging.getLogger(__name__)


class GenericHapiPipeline(MongoLoaderBase):
    """
    Orchestrates the data streaming, metadata generation, and data loading
    using the generic HAPI client, which reads its catalog from MongoDB.
    """

    def __init__(
        self,
        db: Database,
        base_output_dir: pathlib.Path,
    ):
        """
        Initializes the pipeline.

        Args:
            db (Database): The MongoDB database instance.
            base_output_dir (pathlib.Path): Root directory for any potential
                                            data file outputs (if needed).
                                            Metadata is now saved to DB.
        """
        super().__init__(db)
        self.client = GenericHapiClient(db)
        self.output_dir = base_output_dir
        self.metadata_writer = MetadataWriter(db)
        if not self.output_dir.exists():
            self.output_dir.mkdir(parents=True)

    def _normalize_hapi_meta_to_variables(self, hapi_meta: Dict[str, Any]) -> List[VariableMetadata]:
        """
        Converts the raw HAPI metadata dictionary into a list of
        standardized VariableMetadata objects.

        Args:
            hapi_meta (Dict[str, Any]): The raw metadata dict from hapiclient.

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

    def _transform_timeseries_to_docs(self, ts: sunpy.timeseries.TimeSeries, source_info: str) -> List[Dict[str, Any]]:
        """
        Converts a SunPy TimeSeries into a list of MongoDB documents.

        Args:
            ts (sunpy.timeseries.TimeSeries): The 1D TimeSeries data.
            source_info (str): A string identifying the data source.

        Returns:
            List[Dict[str, Any]]: A list of formatted MongoDB documents.
        """
        logger.info(f"Transforming {len(ts.to_table())} TimeSeries rows to MongoDB documents...")
        documents = []

        df = ts.data

        # Iterate over DataFrame rows
        for timestamp, row in df.iterrows():
            data_payload = {k: v for k, v in row.items() if not pd.isna(v)}

            # Ensure timestamp is a native Python datetime
            ts_py: datetime = timestamp.to_pydatetime()

            documents.append(
                {
                    "timestamp": ts_py,
                    "metadata": {"source": source_info},
                    "data": data_payload,
                }
            )
        logger.info(f"Transformed {len(documents)} documents.")
        return documents

    def _sanitize_collection_name(self, dataset_id: str) -> str:
        """
        Replaces characters that are invalid in MongoDB collection names.
        """
        # Replace / and - with _
        return dataset_id.replace("/", "_").replace("-", "_")

    def run(
        self,
        datasets_to_process: List[str],
        start_time: datetime,
        stop_time: datetime,
    ):
        """
        Runs the full HAPI streaming pipeline for a list of datasets.

        Args:
            datasets_to_process (List[str]): A list of dataset IDs to fetch.
            start_time (datetime): The start time for the data query.
            stop_time (datetime): The end time for the data query.
        """
        logger.info("--- Starting Generic HAPI Pipeline ---")
        if not self.client.catalog_map:
            logger.error("HAPI client has no catalog map loaded from DB. Aborting pipeline.")
            return

        logger.info(f"Processing {len(datasets_to_process)} datasets...")

        # Ensure config times are timezone-aware (UTC)
        config_start = start_time
        if config_start.tzinfo is None:
            config_start = config_start.replace(tzinfo=timezone.utc)

        config_stop = stop_time
        if config_stop.tzinfo is None:
            config_stop = config_stop.replace(tzinfo=timezone.utc)

        for dataset_id in datasets_to_process:
            logger.info(f"--- Processing Dataset: {dataset_id} ---")

            try:
                data, meta = self.client.get_data_and_meta(dataset_id, config_start, config_stop)

                if data is None or meta is None:
                    logger.warning(
                        f"Skipping {dataset_id}: Could not retrieve data. "
                        "All fallbacks failed or no time overlap found."
                    )
                    continue

                ts = convert_hapi_to_timeseries(data, meta)

                # --- Data Loading Logic ---
                if ts:
                    logger.info(
                        f"Successfully converted {dataset_id} data to "
                        f"sunpy.timeseries.TimeSeries with {len(ts.to_table())} rows."
                    )

                    # Transform
                    source_url = meta.get("x_server_url", "HAPI")
                    documents = self._transform_timeseries_to_docs(ts, source_info=source_url)

                    # Get safe collection name
                    collection_name = self._sanitize_collection_name(dataset_id)

                    # Ensure Collection Exists (inherited)
                    self._ensure_timeseries_collection(collection_name, time_field="timestamp", meta_field="metadata")

                    # Load Data (inherited)
                    self._load_data(collection_name, documents)

                else:
                    logger.warning(f"Failed to convert {dataset_id} to TimeSeries. Cannot load to DB.")
                # --- End Data Loading ---

                # --- Metadata Saving (Schema) ---
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
                # --- End Metadata Saving ---

            except Exception as e:
                logger.error(
                    f"Pipeline failed unexpectedly for {dataset_id}: {e}",
                    exc_info=True,
                )

            logger.info(f"--- Finished Dataset: {dataset_id} ---")

        logger.info("--- Generic HAPI Pipeline Finished ---")
