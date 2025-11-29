import logging
import pathlib
import re
from typing import Dict, List

import numpy as np
import pandas as pd
from pymongo.database import Database

from space_weather_analysis.pipelines.processors.base import BaseETLProcessor
from space_weather_analysis.util.types import RawData, TransformedDocuments

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
        "central_pa",  # (Central Position Angle): Angle at the sun edge from which the CME originated
        "width",  # Angle at which the CME spreads
        "linear_speed",  # Average speed
        "spd_initial",  # Starting speed
        "spd_final",  # Ending speed
        "spd_20r",  # Speed after a distance of 20 sun radiants
        "accel",  # (De)acceleration
        "mass",  # Estimated mass carried by the cme in gramms
        "kinetic_energy",  # Estimated energy carried by the cme in erg (https://en.wikipedia.org/wiki/Erg)
        "mpa",  # (Measurement Position Angle): Angle from which the speed was meassured
        "remarks",  # extra comments C2 and C3 are different telescopes, poor events have low data quality, points are the amount of measurements
    ]

    def __init__(self, db: Database, file_path: pathlib.Path):
        """
        Initializes the CME ETL processor.

        Args:
            db (Database): The MongoDB database instance.
            file_path (pathlib.Path): Path to the CME catalog text file.
        """
        super().__init__(db)
        self.file_path = file_path

    def _extract(self) -> RawData:
        """
        Extracts CME data from the local space-delimited text file.

        Returns:
            Optional[pd.DataFrame]: A DataFrame containing the raw data,
                                    or None if extraction fails.
        """
        logger.info(f"--- Starting Extract for LASCO CME: {self.file_path} ---")
        if not self.file_path.exists():
            logger.error(f"CME catalog file not found at: {self.file_path}")
            return None

        try:
            parsed_data: List[Dict[str, str]] = []
            with open(self.file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                # Skip the header rows
                for line in lines[4:]:
                    # Normalize whitespace to single spaces for splitting
                    line_normalized = re.sub(r"\s+", " ", line).strip()
                    if not line_normalized:
                        continue
                    try:
                        # Split into 13 columns (maxsplit=12)
                        parts = line_normalized.split(" ", maxsplit=12)
                        if len(parts) == 12:
                            parts.append("")  # Ensure remarks column exists
                        parsed_data.append(dict(zip(self._COLUMN_NAMES, parts, strict=False)))
                    except Exception as e:
                        logger.warning(f"Could not parse line: {line.strip()} | Error: {e}")

            df = pd.DataFrame(parsed_data)
            logger.info(f"Extracted and parsed {len(df)} rows from CME catalog.")
            return df
        except Exception as e:
            logger.error(f"Error reading CME file: {e}", exc_info=True)
            return None

    def _transform(self, raw_data: RawData) -> TransformedDocuments:
        """
        Transforms the raw CME DataFrame into MongoDB documents.

        Args:
            raw_data (Optional[pd.DataFrame]): The raw data from _extract().

        Returns:
            List[Dict[str, Any]]: A list of formatted dictionaries (documents)
                                  for MongoDB.
        """
        if raw_data is None:
            logger.warning("Transform step skipped as no data was extracted.")
            return []

        logger.info("--- Starting Transform for CME Catalog ---")
        # Replace placeholder strings with NaN
        raw_data = raw_data.replace(r"-------", np.nan, regex=True)
        raw_data = raw_data.replace(r"\*.*", "", regex=True)

        try:
            # Create the primary 'timestamp' field
            raw_data["timestamp"] = pd.to_datetime(
                raw_data["date"].astype(str) + " " + raw_data["time"].astype(str),
                format="%Y/%m/%d %H:%M:%S",
                errors="coerce",
            )
            # Remove rows where timestamp creation failed
            raw_data.dropna(subset=["timestamp"], inplace=True)
            logger.info(f"Removed rows with invalid timestamps. New count: {len(raw_data)}")
        except Exception as e:
            logger.error(f"Error creating timestamp for CMEs: {e}", exc_info=True)
            return []

        # Feature engineering: extract 'is_halo' boolean
        raw_data["is_halo"] = raw_data["central_pa"].str.contains("Halo", na=False)

        # Convert numeric columns, coercing errors to NaN
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
        for col in numeric_cols:
            raw_data[col] = pd.to_numeric(raw_data[col], errors="coerce")

        # Replace NaNs with a specific value (-1) for numeric types
        raw_data[numeric_cols] = raw_data[numeric_cols].fillna(-1)
        raw_data["remarks"] = raw_data["remarks"].fillna("")

        # Rename columns for the final data payload
        raw_data.rename(
            columns={
                "spd_initial": "speed_initial",
                "spd_final": "speed_final",
                "spd_20r": "speed_at_20r",
            },
            inplace=True,
        )

        # Define the columns that will be nested in the 'data' field
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

        # Vectorized document creation
        data_payloads = raw_data[data_columns].to_dict("records")
        timestamps = raw_data["timestamp"].to_list()
        metadata = {"source": "SOHO/LASCO_CDAW_V2.0"}

        documents: TransformedDocuments = [
            {"timestamp": ts, "metadata": metadata, "data": data}
            for ts, data in zip(timestamps, data_payloads, strict=False)
        ]

        logger.info(f"Transformed {len(documents)} CME documents.")
        return documents

    def run(self) -> None:
        """
        Main orchestration method for the CME ETL process.
        """
        logger.info("--- Starting ETL process for CME Catalog ---")

        raw_data_df = self._extract()
        documents = self._transform(raw_data_df)

        # Define time-series collection properties
        self._ensure_timeseries_collection(
            self.COLLECTION_NAME,
            time_field="timestamp",
            meta_field="metadata",
            bucketMaxSpanSeconds=86400,  # 1 day
            bucketRoundingSeconds=86400,
        )

        self._load_data(self.COLLECTION_NAME, documents, self.DEFAULT_BATCH_SIZE)
        logger.info("--- Finished ETL process for CME Catalog ---")
