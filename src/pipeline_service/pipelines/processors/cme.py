import logging
import pathlib
from typing import Dict, List, Optional

import pandas as pd
from pymongo.database import Database

from pipeline_service.pipelines.processors.base import BaseETLProcessor
from space_weather_analysis.util.types import TransformedDocuments

logger = logging.getLogger(__name__)


class CmeETL(BaseETLProcessor):
    """ETL Processor for Coronal Mass Ejection (CME) catalog data."""

    COLLECTION_NAME = "cme_catalog"
    DEFAULT_BATCH_SIZE = 5000

    _NUMERIC_COLUMNS = [
        "central_pa",
        "width",
        "linear_speed",
        "speed_initial",
        "speed_final",
        "speed_at_20r",
        "accel",
        "mass",
        "kinetic_energy",
        "mpa",
    ]
    # Column order of the catalog file; the numeric columns sit between time and remarks.
    _COLUMN_NAMES = ["date", "time", *_NUMERIC_COLUMNS, "remarks"]
    # Dash runs that the catalog writes in place of an unknown value.
    _PLACEHOLDERS = {"----", "------", "-------"}
    # Columns in which a trailing '*' marks an uncertain value, mapped to the flag field storing that marker.
    _MARKER_FLAGS = {
        "accel": "accel_unreliable",
        "mass": "mass_uncertain",
        "kinetic_energy": "kinetic_energy_uncertain",
    }

    def __init__(self, db: Database, file_path: pathlib.Path) -> None:
        """Initializes the CME ETL processor.

        Args:
            db: The MongoDB database instance.
            file_path: Path to the CME catalog text file.
        """
        super().__init__(db)
        self.file_path = file_path

    def _extract(self) -> Optional[pd.DataFrame]:
        """Extracts CME data from the local space-delimited text file.

        Returns:
            A DataFrame containing the raw data, or None if extraction fails.
        """
        logger.info(f"--- Starting Extract for LASCO CME: {self.file_path} ---")
        if not self.file_path.exists():
            logger.error(f"CME catalog file not found at: {self.file_path}")
            return None

        try:
            parsed_data: List[Dict[str, str]] = []
            with open(self.file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                # Skip the header rows (first 4 lines)
                for line in lines[4:]:
                    # The remarks are the rest of the line after the 12th column and keep their inner spacing.
                    parts = line.rstrip().split(maxsplit=12)
                    if not parts:
                        continue
                    # Rows without remarks have 12 parts and get no remarks entry.
                    parsed_data.append(dict(zip(self._COLUMN_NAMES, parts, strict=False)))

            df = pd.DataFrame(parsed_data, columns=self._COLUMN_NAMES)
            logger.info(f"Extracted and parsed {len(df)} rows from CME catalog.")
            return df
        except Exception as e:
            logger.error(f"Error reading CME file: {e}", exc_info=True)
            return None

    def _transform(self, raw_data: Optional[pd.DataFrame]) -> TransformedDocuments:
        """Transforms the raw CME DataFrame into MongoDB documents.

        Args:
            raw_data: The raw data from extraction.

        Returns:
            A list of formatted dictionaries for MongoDB.
        """
        if raw_data is None:
            logger.warning("Transform step skipped as no data was extracted.")
            return []

        logger.info("--- Starting Transform for CME Catalog ---")
        for column, flag in self._MARKER_FLAGS.items():
            is_marked = raw_data[column].str.endswith("*", na=False)
            # The flag is stored as true for marked values only; unmarked events get no flag field.
            raw_data[flag] = is_marked.map({True: True, False: None})
            raw_data[column] = raw_data[column].str.removesuffix("*")

        raw_data["timestamp"] = pd.to_datetime(
            raw_data["date"].astype(str) + " " + raw_data["time"].astype(str),
            format="%Y/%m/%d %H:%M:%S",
            errors="coerce",
        )
        has_valid_timestamp = raw_data["timestamp"].notna()
        if not has_valid_timestamp.all():
            logger.warning(f"Skipped {(~has_valid_timestamp).sum()} rows without a valid timestamp.")
        raw_data.dropna(subset=["timestamp"], inplace=True)

        # Feature engineering
        raw_data["is_halo"] = raw_data["central_pa"].str.contains("Halo", na=False)
        # A halo CME has no central position angle; is_halo carries that information.
        raw_data["central_pa"] = raw_data["central_pa"].mask(raw_data["is_halo"])

        for col in self._NUMERIC_COLUMNS:
            is_placeholder = raw_data[col].isin(self._PLACEHOLDERS)
            numeric = pd.to_numeric(raw_data[col].mask(is_placeholder), errors="coerce")
            # Values that are neither numbers nor placeholders are invalid and omitted like missing ones.
            invalid_count = (numeric.isna() & raw_data[col].notna() & ~is_placeholder).sum()
            if invalid_count:
                logger.warning(f"Omitted {invalid_count} invalid values in field {col}.")
            raw_data[col] = numeric

        data_columns = [
            *self._NUMERIC_COLUMNS,
            "is_halo",
            *self._MARKER_FLAGS.values(),
            "remarks",
        ]

        data_payloads = raw_data[data_columns].to_dict("records")
        timestamps = raw_data["timestamp"].to_list()

        documents = self._build_documents(timestamps, data_payloads, "SOHO/LASCO_CDAW_V2.0")

        logger.info(f"Transformed {len(documents)} CME documents.")
        return documents

    def run(self) -> None:
        """Orchestrates the ETL process for CME data."""
        logger.info("--- Starting ETL process for CME Catalog ---")

        raw_data_df = self._extract()
        documents = self._transform(raw_data_df)

        if documents:
            self._ensure_timeseries_collection(
                self.COLLECTION_NAME,
                time_field="timestamp",
                meta_field="metadata",
            )
            # The file holds the complete dataset and records have no unique key, so each run replaces the collection content.
            self._load_data(self.COLLECTION_NAME, documents, self.DEFAULT_BATCH_SIZE, clear_collection=True)

        logger.info("--- Finished ETL process for CME Catalog ---")
