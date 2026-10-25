import logging
import pathlib
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from pymongo.database import Database

from pipeline_service.pipelines.processors.base import BaseETLProcessor

logger = logging.getLogger(__name__)


class SilsoETL(BaseETLProcessor):
    """ETL Processor for daily sunspot data (SILSO).

    Parses a CSV-like file containing sunspot numbers and loads them into MongoDB.
    """

    COLLECTION_NAME = "silso_sunspot_daily"
    DEFAULT_BATCH_SIZE = 5000

    _COLUMN_NAMES = [
        "year",
        "month",
        "day",
        "decimal_year",
        "sunspot_number",
        "std_dev",
        "num_observations",
        "indicator",
    ]

    def __init__(self, db: Database, file_path: pathlib.Path) -> None:
        """Initializes the sunspot data ETL processor.

        Args:
            db: The MongoDB database instance.
            file_path: Path to the sunspot data CSV file.
        """
        super().__init__(db)
        self.file_path = file_path

    def _extract(self) -> Optional[pd.DataFrame]:
        """Extracts sunspot data from the local CSV file.

        Returns:
            A DataFrame containing the raw data, or None if extraction fails.
        """
        logger.info(f"--- Starting Extract for SILSO: {self.file_path} ---")
        if not self.file_path.exists():
            logger.error(f"SILSO data file not found at: {self.file_path}")
            return None

        try:
            df = pd.read_csv(
                self.file_path,
                sep=";",
                names=self._COLUMN_NAMES,
                dtype={"year": int, "month": int, "day": int},
                skipinitialspace=True,
            )
            logger.info(f"Extracted {len(df)} rows from CSV.")
            return df
        except Exception as e:
            logger.error(f"Error reading SILSO file: {e}", exc_info=True)
            return None

    def _transform(self, raw_data: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
        """Transforms the raw sunspot DataFrame into MongoDB documents.

        Args:
            raw_data: The raw data from the extraction step.

        Returns:
            A list of formatted dictionaries for MongoDB.
        """
        if raw_data is None:
            logger.warning("Transform step skipped as no data was extracted.")
            return []

        logger.info("--- Starting Transform for SILSO ---")
        # Replace -1 (missing value indicator) with NaN
        raw_data["sunspot_number"] = raw_data["sunspot_number"].replace(-1, np.nan)

        # Combine year, month, and day into a timestamp
        raw_data["timestamp"] = pd.to_datetime(raw_data[["year", "month", "day"]])
        # SILSO marks provisional values with indicator 0 and definitive values with 1.
        raw_data["is_provisional"] = raw_data["indicator"] == 0

        data_columns = [
            "sunspot_number",
            "std_dev",
            "num_observations",
            "is_provisional",
            "decimal_year",
        ]

        data_payloads = raw_data[data_columns].to_dict("records")
        timestamps = raw_data["timestamp"].to_list()
        metadata = {"source": "SILSO_V2.0"}

        documents = [
            {
                "timestamp": ts,
                "metadata": metadata,
                "data": {k: v for k, v in data_dict.items() if not pd.isna(v)},
            }
            for ts, data_dict in zip(timestamps, data_payloads, strict=False)
        ]

        logger.info(f"Transformed {len(documents)} documents.")
        return documents

    def run(self) -> None:
        """Orchestrates the ETL process for sunspot data."""
        logger.info("--- Starting ETL process for SILSO ---")

        raw_data_df = self._extract()
        documents = self._transform(raw_data_df)

        if documents:
            self._ensure_timeseries_collection(
                self.COLLECTION_NAME,
                time_field="timestamp",
                meta_field="metadata",
            )
            self._load_data(self.COLLECTION_NAME, documents, self.DEFAULT_BATCH_SIZE)

        logger.info("--- Finished ETL process for SILSO ---")
