import logging
import re
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from pymongo.database import Database

from .base_processor import BaseETLProcessor  # Import the base class

logger = logging.getLogger(__name__)


class CmeETL(BaseETLProcessor):
    """
    ETL Processor for the SOHO/LASCO CME Catalog.
    """

    FILE_PATH = "data/cme_catalog.txt"
    COLLECTION_NAME = "cme_catalog"
    DEFAULT_BATCH_SIZE = 5000

    def __init__(self, db: Database):
        """
        Initializes the CME ETL processor.

        Args:
            db (Database): The MongoDB database instance.
        """
        super().__init__(db)

    def _extract(self) -> Optional[pd.DataFrame]:
        """
        Extracts CME data from the local space-delimited text file.

        Returns:
            Optional[pd.DataFrame]: A DataFrame containing the raw data,
                                    or None if extraction fails.
        """
        logger.info(f"--- Starting Extract for LASCO CME: {self.FILE_PATH} ---")
        try:
            col_names = [
                "date",
                "time",
                "central_pa",  # (Central Position Angle): Angle at the sun edge from which the CME originated
                "width",  # Angle at which the CME spreads
                "linear_speed",  # Average speed
                "spd_initial",  # Starting speed
                "spd_final",  # Ending speed
                "spd_20r",  # Speed after a distance of 20 sun radiants
                "accel",  # (De)acceleration
                "mass",  # estimated mass carried by the cme in gramms
                "kinetic_energy",  # estimated energy carried by the cme in erg (https://en.wikipedia.org/wiki/Erg)
                "mpa",  # (Measurement Position Angle): Angle from which the speed was meassured
                "remarks",  # extra comments C2 and C3 are different telescopes, poor events have low data quality, points are the amount of measurements
            ]

            parsed_data = []
            with open(self.FILE_PATH, "r") as f:
                lines = f.readlines()
                for line in lines[4:]:  # Start iterating after the header
                    line_normalized = re.sub(r"\s+", " ", line).strip()
                    if not line_normalized:
                        continue
                    try:
                        parts = line_normalized.split(" ", maxsplit=12)
                        if len(parts) == 12:
                            parts.append("")
                        parsed_data.append(dict(zip(col_names, parts)))
                    except Exception as e:
                        logger.warning(f"Could not parse line: {line.strip()} | Error: {e}")
            df = pd.DataFrame(parsed_data)
            logger.info(f"Extracted and parsed {len(df)} rows from CME catalog.")
            return df
        except FileNotFoundError:
            logger.error(f"CME catalog file not found at: {self.FILE_PATH}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Error reading CME file: {e}", exc_info=True)
            return None

    def _transform(self, raw_data: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
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
        raw_data = raw_data.replace(r"-------", np.nan, regex=True)
        raw_data = raw_data.replace(r"\*.*", "", regex=True)

        try:
            raw_data["timestamp"] = pd.to_datetime(
                raw_data["date"] + " " + raw_data["time"],
                format="%Y/%m/%d %H:%M:%S",
                errors="coerce",
            )
            raw_data.dropna(subset=["timestamp"], inplace=True)
            logger.info(f"Removed rows with invalid timestamps. New count: {len(raw_data)}")
        except Exception as e:
            logger.error(f"Error creating timestamp for CMEs: {e}", exc_info=True)
            return []

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
        for col in numeric_cols:
            raw_data[col] = pd.to_numeric(raw_data[col], errors="coerce")
        raw_data[numeric_cols] = raw_data[numeric_cols].fillna(-1)
        raw_data["remarks"] = raw_data["remarks"].fillna("")

        documents = []
        for _, row in raw_data.iterrows():
            data = {
                "central_pa": row["central_pa"],
                "width": row["width"],
                "linear_speed": row["linear_speed"],
                "speed_initial": row["spd_initial"],
                "speed_final": row["spd_final"],
                "speed_at_20r": row["spd_20r"],
                "accel": row["accel"],
                "mass": row["mass"],
                "kinetic_energy": row["kinetic_energy"],
                "mpa": row["mpa"],
                "is_halo": row["is_halo"],
                "remarks": row["remarks"].strip(),
            }
            documents.append(
                {
                    "timestamp": row["timestamp"],
                    "metadata": {"source": "SOHO/LASCO_CDAW_V2.0"},
                    "data": {k: v for k, v in data.items()},
                }
            )
        logger.info(f"Transformed {len(documents)} CME documents.")
        return documents

    def run(self):
        """
        Main orchestration method for the CME ETL process.
        """
        logger.info("--- Starting ETL process for CME Catalog ---")
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
        logger.info("--- Finished ETL process for CME Catalog ---")
