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
    """
    Performs the ETL process for the SOHO/LASCO CME Catalog.

    This function orchestrates the three stages:
    1.  **Extract**: Reads the space-delimited text file from
        'data/cme_catalog.txt'. This requires a custom parsing
        logic as the file is not a standard CSV.
    2.  **Transform**: Cleans the data (removes '-------', '*'),
        converts types, parses timestamps, and structures it
        into MongoDB documents.
    3.  **Load**: Inserts the transformed documents into the
        'cme_catalog' collection in MongoDB.

    Args:
        db (pymongo.database.Database): The MongoDB database instance where
                                        the data will be loaded.
    """
    logger.info(f"--- Starting ETL for LASCO CME: {FILE_PATH} ---")

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

        with open(FILE_PATH, "r") as f:
            lines = f.readlines()

            # Start iterating after the header (first 4 lines)
            for line in lines[4:]:
                # Normalize all whitespace (spaces/tabs) to a single space
                line_normalized = re.sub(r"\s+", " ", line).strip()

                if not line_normalized:
                    continue  # Skip empty lines

                try:
                    # Split the line only on the first 12 spaces.
                    # 'maxsplit=12' ensures that all remaining text,
                    # including spaces, is assigned to the 13th item (remarks).
                    parts = line_normalized.split(" ", maxsplit=12)

                    if len(parts) == 12:
                        # Add an empty string if 'remarks' is missing
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

    # Replace '-------' (used as a null marker) with NaN for processing
    df = df.replace(r"-------", np.nan, regex=True)

    # Remove uncertainty markers
    df = df.replace(r"\*.*", "", regex=True)

    try:
        # Combine date and time columns into a single timestamp
        df["timestamp"] = pd.to_datetime(
            df["date"] + " " + df["time"],
            format="%Y/%m/%d %H:%M:%S",
            errors="coerce",  # Set invalid dates (e.g., 24:00:00) to NaT
        )
        # Remove rows where timestamp parsing failed
        df.dropna(subset=["timestamp"], inplace=True)
        logger.info(f"Removed rows with invalid timestamps. New count: {len(df)}")
    except Exception as e:
        logger.error(f"Error creating timestamp for CMEs: {e}")
        return

    # Extract Halo CME flag (central_pa is 360: cme spreads in every direction)
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
    # Convert all numeric columns, handling scientific notation (e.g., 7.1e+13)
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Replace any conversion errors (NaN) with -1
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

        # Structure for MongoDB Time Series
        documents.append(
            {
                "timestamp": row["timestamp"],
                "metadata": {"source": "SOHO/LASCO_CDAW_V2.0"},
                # We keep all data fields, including -1 and empty strings
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
            # Create as a time series collection, optimized for daily bucketing
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
        # Clear the collection to ensure the ETL process is idempotent
        logger.info(f"Clearing existing data in {COLLECTION_NAME}...")
        collection.delete_many({})

        logger.info(f"Loading {len(documents)} CME documents in batches of {DEFAULT_BATCH_SIZE}...")

        total_inserted = 0
        preprocessor = DataPreprocessor(batch_size=DEFAULT_BATCH_SIZE)
        # Use the batch generator to insert data in chunks
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
