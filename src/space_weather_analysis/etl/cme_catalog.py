import logging
import re
import pandas as pd
import numpy as np

from pymongo.database import Database
from pymongo.errors import BulkWriteError

from space_weather_analysis.util.data_preprocessing import DataPreprocessor

logger = logging.getLogger(__name__)

FILE_PATH = "data/cme_catalog.txt"
COLLECTION_NAME = "cme_catalog"
DEFAULT_BATCH_SIZE = 5000


def etl_cme_data(db: Database):
    logger.info(f"--- Starting ETL for LASCO CME: {FILE_PATH} ---")

    try:
        col_names = [
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

        parsed_data = []

        with open(FILE_PATH, "r") as f:
            lines = f.readlines()

            # start after header
            for line in lines[4:]:
                line_normalized = re.sub(r"\s+", " ", line).strip()

                if not line_normalized:
                    continue

                try:
                    parts = line_normalized.split(" ", maxsplit=12)

                    if len(parts) == 12:
                        # Add empty remarks
                        parts.append("")

                    parsed_data.append(dict(zip(col_names, parts)))

                except Exception as e:
                    logger.warning(f"Could not parse line: {line.strip()} | Error: {e}")

        df = pd.DataFrame(parsed_data)
        logger.info(f"Extracted and parsed {len(df)} rows from CME catalog.")

    except FileNotFoundError:
        logger.error(f"CME catalog file not found at: {FILE_PATH}")
        return
    except Exception as e:
        logger.error(f"Error reading CME file: {e}", exc_info=True)
        return

    df = df.replace(r"-------", np.nan, regex=True)

    df = df.replace(r"\*.*", "", regex=True)

    try:
        df["timestamp"] = pd.to_datetime(df["date"] + " " + df["time"], format="%Y/%m/%d %H:%M:%S", errors="coerce")
        df.dropna(subset=["timestamp"], inplace=True)
        logger.info(f"Removed rows with invalid timestamps. New count: {len(df)}")
    except Exception as e:
        logger.error(f"Error creating timestamp for CMEs: {e}")
        return

    df["is_halo"] = df["central_pa"].str.contains("Halo", na=False)

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
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df[numeric_cols] = df[numeric_cols].fillna(-1)
    df["remarks"] = df["remarks"].fillna("")

    documents = []
    for _, row in df.iterrows():
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

    if not documents:
        logger.info("No CME documents to load.")
        return

    collection = db[COLLECTION_NAME]

    if COLLECTION_NAME not in db.list_collection_names():
        logger.info(f"Creating new time series collection: {COLLECTION_NAME}")
        db.create_collection(
            COLLECTION_NAME,
            timeseries={
                "timeField": "timestamp",
                "metaField": "metadata",
                "bucketMaxSpanSeconds": 86400,  # 1 day
                "bucketRoundingSeconds": 86400,
            },
        )
    else:
        logger.info(f"Collection {COLLECTION_NAME} already exists.")

    try:
        logger.info(f"Clearing existing data in {COLLECTION_NAME}...")
        collection.delete_many({})

        logger.info(f"Loading {len(documents)} CME documents in batches of {DEFAULT_BATCH_SIZE}...")

        total_inserted = 0
        preprocessor = DataPreprocessor(batch_size=DEFAULT_BATCH_SIZE)
        for i, batch in enumerate(preprocessor.batch_generator(documents)):
            if not batch:
                continue

            collection.insert_many(batch, ordered=False)
            total_inserted += len(batch)
            logger.info(f"  ... inserted batch {i + 1} ({total_inserted}/{len(documents)} documents)")

        logger.info(f"CME data load complete. Total inserted: {total_inserted}")

    except BulkWriteError as bwe:
        logger.error(f"Error during CME bulk insert: {bwe.details}")
    except Exception as e:
        logger.error(f"Error loading CME data to MongoDB: {e}", exc_info=True)
