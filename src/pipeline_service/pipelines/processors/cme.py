import logging
import pathlib
import re
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from pymongo.database import Database

from pipeline_service.pipelines.processors.base import BaseETLProcessor

logger = logging.getLogger(__name__)


class CmeETL(BaseETLProcessor):
    """
    ETL Processor for Coronal Mass Ejection (CME) catalog data.
    """

    COLLECTION_NAME = "cme_catalog"
    DEFAULT_BATCH_SIZE = 5000

    _COLUMN_NAMES = [
        "date",
        "time",
        "central_pa",
        "width",
        "linear_speed",
        "spd_initial",
        "spd_final",
        "spd_20r",
        "accel",
        "mass",
        "kinetic_energy",
        "mpa",
        "remarks",
    ]

    def __init__(self, db: Database, file_path: pathlib.Path) -> None:
        """
        Initializes the CME ETL processor.

        Args:
            db: The MongoDB database instance.
            file_path: Path to the CME catalog text file.
        """
        super().__init__(db)
        self.file_path = file_path

    def _extract(self) -> Optional[pd.DataFrame]:
        """
        Extracts CME data from the local space-delimited text file.

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
                    line_normalized = re.sub(r"\s+", " ", line).strip()
                    if not line_normalized:
                        continue
                    try:
                        # Split into columns, accounting for potential variable whitespace
                        parts = line_normalized.split(" ", maxsplit=12)
                        if len(parts) == 12:
                            parts.append("")  # Handle missing remarks
                        parsed_data.append(dict(zip(self._COLUMN_NAMES, parts, strict=False)))
                    except Exception as e:
                        logger.warning(f"Could not parse line: {line.strip()} | Error: {e}")

            df = pd.DataFrame(parsed_data)
            logger.info(f"Extracted and parsed {len(df)} rows from CME catalog.")
            return df
        except Exception as e:
            logger.error(f"Error reading CME file: {e}", exc_info=True)
            return None

    def _transform(self, raw_data: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
        """
        Transforms the raw CME DataFrame into MongoDB documents.

        Args:
            raw_data: The raw data from extraction.

        Returns:
            A list of formatted dictionaries for MongoDB.
        """
        if raw_data is None:
            logger.warning("Transform step skipped as no data was extracted.")
            return []

        logger.info("--- Starting Transform for CME Catalog ---")
        # Clean placeholders
        raw_data = raw_data.replace(r"-------", np.nan, regex=True)
        raw_data = raw_data.replace(r"\*.*", "", regex=True)

        try:
            raw_data["timestamp"] = pd.to_datetime(
                raw_data["date"].astype(str) + " " + raw_data["time"].astype(str),
                format="%Y/%m/%d %H:%M:%S",
                errors="coerce",
            )
            raw_data.dropna(subset=["timestamp"], inplace=True)
        except Exception as e:
            logger.error(f"Error creating timestamp for CMEs: {e}", exc_info=True)
            return []

        # Feature engineering
        raw_data["is_halo"] = raw_data["central_pa"].str.contains("Halo", na=False)

        numeric_cols = [
            "width",
            "linear_speed",
            "spd_initial",
            "spd_final",
            "spd_20r",
            "accel",
            "mass",
            "kinetic_energy",
            "mpa",
        ]

        # Coerce numeric conversions
        for col in numeric_cols:
            raw_data[col] = pd.to_numeric(raw_data[col], errors="coerce")

        raw_data.rename(
            columns={
                "spd_initial": "speed_initial",
                "spd_final": "speed_final",
                "spd_20r": "speed_at_20r",
            },
            inplace=True,
        )

        data_columns = [
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
            "is_halo",
            "remarks",
        ]

        data_payloads = raw_data[data_columns].to_dict("records")
        timestamps = raw_data["timestamp"].to_list()
        metadata = {"source": "SOHO/LASCO_CDAW_V2.0"}

        documents = [{"timestamp": ts, "metadata": metadata, "data": data} for ts, data in zip(timestamps, data_payloads, strict=False)]

        logger.info(f"Transformed {len(documents)} CME documents.")
        return documents

    def run(self) -> None:
        """
        Orchestrates the ETL process for CME data.
        """
        logger.info("--- Starting ETL process for CME Catalog ---")

        raw_data_df = self._extract()
        documents = self._transform(raw_data_df)

        if documents:
            self._ensure_timeseries_collection(
                self.COLLECTION_NAME,
                time_field="timestamp",
                meta_field="metadata",
            )
            self._load_data(self.COLLECTION_NAME, documents, self.DEFAULT_BATCH_SIZE)

        logger.info("--- Finished ETL process for CME Catalog ---")
