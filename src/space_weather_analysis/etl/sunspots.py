import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from pymongo.database import Database

from .base_processor import BaseETLProcessor  # Import the base class

logger = logging.getLogger(__name__)


class SilsoETL(BaseETLProcessor):
    """
    ETL Processor for SILSO daily sunspot data.
    """

    FILE_PATH = "data/SN_d_tot_V2.0.csv"
    COLLECTION_NAME = "silso_sunspot_daily"
    DEFAULT_BATCH_SIZE = 5000

    def __init__(self, db: Database):
        """
        Initializes the SILSO ETL processor.

        Args:
            db (Database): The MongoDB database instance.
        """
        super().__init__(db)  # Initialize the base class

    def _extract(self) -> pd.DataFrame | None:
        """
        Extracts SILSO data from the local CSV file.

        Returns:
            pd.DataFrame or None: A DataFrame containing the raw data,
                                  or None if extraction fails.
        """
        logger.info(f"--- Starting Extract for SILSO: {self.FILE_PATH} ---")
        try:
            col_names = [
                "year",
                "month",
                "day",
                "date_fraction_year",
                "sunspot_number",
                "std_dev",
                "num_observations",
                "indicator",
            ]
            df = pd.read_csv(
                self.FILE_PATH,
                sep=";",
                names=col_names,
                dtype={"year": int, "month": int, "day": int},
            )
            logger.info(f"Extracted {len(df)} rows from CSV.")
            return df
        except FileNotFoundError:
            logger.error(f"SILSO data file not found at: {self.FILE_PATH}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Error reading SILSO file: {e}", exc_info=True)
            return None

    def _transform(self, raw_data: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
        """
        Transforms the raw SILSO DataFrame into MongoDB documents.

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
        # 'indicator' is 0 or 1,
        raw_data["indicator"] = raw_data["indicator"].astype(bool)

        documents = []
        for _, row in raw_data.iterrows():
            data = {
                "sunspot_number": row["sunspot_number"],
                "std_dev": row["std_dev"],
                "num_observations": row["num_observations"],
                "indicator": row["indicator"],
                "date_fraction_year": row["date_fraction_year"],
            }
            documents.append(
                {
                    "timestamp": row["timestamp"],
                    "metadata": {"source": "SILSO_V2.0"},
                    # Filter out NaN values to keep the documents clean.
                    "data": {k: v for k, v in data.items() if not pd.isna(v)},
                }
            )
        logger.info(f"Transformed {len(documents)} documents.")
        return documents

    def run(self):
        """
        Main orchestration method for the SILSO ETL process.
        """
        logger.info("--- Starting ETL process for SILSO ---")

        # Extract
        raw_data_df = self._extract()

        # Transform
        documents = self._transform(raw_data_df)

        # Load
        self._ensure_timeseries_collection(
            self.COLLECTION_NAME,
            time_field="timestamp",
            meta_field="metadata",
            bucketMaxSpanSeconds=86400,  # 1 day
            bucketRoundingSeconds=86400,
        )
        self._load_data(self.COLLECTION_NAME, documents, self.DEFAULT_BATCH_SIZE)
        logger.info("--- Finished ETL process for SILSO ---")
