import logging
import pathlib
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from pymongo.database import Database

from .base_processor import BaseETLProcessor

logger = logging.getLogger(__name__)


class SilsoETL(BaseETLProcessor):
    """
    ETL Processor for daily sunspot data.
    """

    COLLECTION_NAME = "silso_sunspot_daily"
    DEFAULT_BATCH_SIZE = 5000

    # Defines the column names for the CSV file
    _COLUMN_NAMES = [
        "year",
        "month",
        "day",
        "date_fraction_year",
        "sunspot_number",
        "std_dev",
        "num_observations",
        "indicator",
    ]

    def __init__(self, db: Database, file_path: pathlib.Path):
        """
        Initializes the sunspot data ETL processor.

        Args:
            db (Database): The MongoDB database instance.
            file_path (pathlib.Path): Path to the sunspot data CSV file.
        """
        super().__init__(db)
        self.file_path = file_path

    def _extract(self) -> Optional[pd.DataFrame]:
        """
        Extracts sunspot data from the local CSV file.

        Returns:
            Optional[pd.DataFrame]: A DataFrame containing the raw data,
                                    or None if extraction fails.
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
        """
        Transforms the raw sunspot DataFrame into MongoDB documents.

        Args:
            raw_data (Optional[pd.DataFrame]): The raw data from _extract().

        Returns:
            List[Dict[str, Any]]: A list of formatted dictionaries (documents)
                                  for MongoDB.
        """
        if raw_data is None:
            logger.warning("Transform step skipped as no data was extracted.")
            return []

        logger.info("--- Starting Transform for SILSO ---")
        # -1 indicates a missing value.
        raw_data["sunspot_number"] = raw_data["sunspot_number"].replace(-1, np.nan)
        # Combine year, month, and day into a single timestamp for MongoDB.
        raw_data["timestamp"] = pd.to_datetime(raw_data[["year", "month", "day"]])
        # 'indicator' is 0 or 1
        raw_data["indicator"] = raw_data["indicator"].astype(bool)

        # Define the columns that will be nested in the 'data' field
        data_columns = [
            "sunspot_number",
            "std_dev",
            "num_observations",
            "indicator",
            "date_fraction_year",
        ]

        # Vectorized document creation
        data_payloads = raw_data[data_columns].to_dict("records")
        timestamps = raw_data["timestamp"].to_list()
        metadata = {"source": "SILSO_V2.0"}

        documents = [
            {
                "timestamp": ts,
                "metadata": metadata,
                # Filter out NaN values to keep the documents clean.
                "data": {k: v for k, v in data_dict.items() if not pd.isna(v)},
            }
            for ts, data_dict in zip(timestamps, data_payloads)
        ]

        logger.info(f"Transformed {len(documents)} documents.")
        return documents

    def run(self) -> None:
        """
        Main orchestration method for the sunspot data ETL process.
        """
        logger.info("--- Starting ETL process for SILSO ---")

        raw_data_df = self._extract()
        documents = self._transform(raw_data_df)

        self._ensure_timeseries_collection(
            self.COLLECTION_NAME,
            time_field="timestamp",
            meta_field="metadata",
            bucketMaxSpanSeconds=86400,  # 1 day
            bucketRoundingSeconds=86400,
        )

        self._load_data(self.COLLECTION_NAME, documents, self.DEFAULT_BATCH_SIZE)
        logger.info("--- Finished ETL process for SILSO ---")
