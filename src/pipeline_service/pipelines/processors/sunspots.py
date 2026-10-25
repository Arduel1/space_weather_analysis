import logging
import pathlib
from typing import Any, Dict, List, Optional

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
    # Fields that hold placeholders instead of measurements on days without a sunspot number (-1).
    _FIELDS_UNSET_ON_MISSING_DAY = ["sunspot_number", "std_dev", "num_observations"]

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
                # Text columns let the transform step convert and validate each field on its own.
                dtype=str,
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
        # An unparsable value becomes NA in its own column only, so the rest of the row stays usable.
        numeric = raw_data.apply(pd.to_numeric, errors="coerce")

        timestamps_all = pd.to_datetime(numeric[["year", "month", "day"]], errors="coerce")
        has_valid_date = timestamps_all.notna()
        if not has_valid_date.all():
            logger.warning(f"Skipped {(~has_valid_date).sum()} rows without a valid date.")
        numeric = numeric[has_valid_date]

        is_missing = numeric["sunspot_number"] == -1
        data = pd.DataFrame(
            {
                "sunspot_number": self._to_count(numeric["sunspot_number"]).mask(is_missing),
                "std_dev": numeric["std_dev"].mask(is_missing),
                "num_observations": self._to_count(numeric["num_observations"]).mask(is_missing),
                # SILSO marks provisional values with indicator 0 and definitive values with 1.
                "is_provisional": numeric["indicator"].map({0: True, 1: False}),
                "decimal_year": numeric["decimal_year"],
            }
        )

        # A field counts as invalid if the file has a value for it that is neither valid nor a documented placeholder.
        given = raw_data.loc[has_valid_date].rename(columns={"indicator": "is_provisional"})[data.columns].notna()
        invalid = data.isna() & given
        invalid.loc[is_missing, self._FIELDS_UNSET_ON_MISSING_DAY] = False
        for column, count in invalid.sum().items():
            if count:
                logger.warning(f"Omitted {count} invalid values in field {column}.")

        data_payloads = data.to_dict("records")
        timestamps = timestamps_all[has_valid_date].to_list()
        metadata = {"source": "SILSO_V2.0"}

        documents = self._build_documents(timestamps, data_payloads, metadata)

        logger.info(f"Transformed {len(documents)} documents.")
        return documents

    @staticmethod
    def _to_count(values: pd.Series) -> pd.Series:
        """Converts whole numbers to a nullable integer column.

        Args:
            values: Numeric values, NA where the file held no parsable number.

        Returns:
            The values as Int64; fractional values become NA.
        """
        return values.where(values % 1 == 0).astype("Int64")

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
            # The file holds the complete dataset including revised provisional values, so each run replaces the collection content.
            self._load_data(self.COLLECTION_NAME, documents, self.DEFAULT_BATCH_SIZE, clear_collection=True)

        logger.info("--- Finished ETL process for SILSO ---")
