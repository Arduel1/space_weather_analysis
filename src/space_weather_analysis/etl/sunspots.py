import logging
import pandas as pd
import numpy as np

from pymongo.database import Database
from pymongo.errors import BulkWriteError

from space_weather_analysis.util.data_preprocessing import DataPreprocessor

logger = logging.getLogger(__name__)

FILE_PATH = "data/SN_d_tot_V2.0.csv"
COLLECTION_NAME = "silso_sunspot_daily"

DEFAULT_BATCH_SIZE = 10000


def etl_silso_data(db: Database):
    """
    Performs the ETL process for SILSO daily sunspot data.

    This function orchestrates the three stages:
    1.  **Extract**: Reads the raw CSV data from 'data/SN_d_tot_V2.0.csv'.
    2.  **Transform**: Cleans the data, converts types, and structures it
        into MongoDB documents.
    3.  **Load**: Inserts the transformed documents into the
        'silso_sunspot_daily' time series collection in MongoDB.

    Args:
        db (pymongo.database.Database): The MongoDB database instance where
                                        the data will be loaded.
    """
    logger.info(f"--- Starting ETL for SILSO: {FILE_PATH} ---")

    try:
        # Column names are based on: https://www.sidc.be/SILSO/infosndtot
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

        df = pd.read_csv(FILE_PATH, sep=";", names=col_names, dtype={"year": int, "month": int, "day": int})
        logger.info(f"Extracted {len(df)} rows from CSV.")

    except FileNotFoundError:
        logger.error(f"SILSO data file not found at: {FILE_PATH}")
        return
    except Exception as e:
        logger.error(f"Error reading SILSO file: {e}")
        return
    # -1 indicates a missing value.
    df["sunspot_number"] = df["sunspot_number"].replace(-1, np.nan)
    # Combine year, month, and day into a single timestamp for MongoDB.
    df["timestamp"] = pd.to_datetime(df[["year", "month", "day"]])
    # 'indicator' is 0 or 1,
    df["indicator"] = df["indicator"].astype(bool)

    documents = []
    for _, row in df.iterrows():
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

    if not documents:
        logger.info("No documents to load.")
        return

    collection = db[COLLECTION_NAME]

    if COLLECTION_NAME not in db.list_collection_names():
        logger.info(f"Creating new time series collection: {COLLECTION_NAME}")
        db.create_collection(
            COLLECTION_NAME,
            # Create as a time series collection, optimized for daily bucketing.
            timeseries={
                "timeField": "timestamp",
                "metaField": "metadata",
                "bucketMaxSpanSeconds": 86400,  # = 1 day
                "bucketRoundingSeconds": 86400,
            },
        )
    else:
        logger.info(f"Collection {COLLECTION_NAME} already exists.")

    try:
        # Clear the collection to ensure the ETL process is idempotent.
        logger.info("Clearing existing data in collection...")
        collection.delete_many({})

        logger.info(f"Loading {len(documents)} documents in batches of {DEFAULT_BATCH_SIZE}...")

        total_inserted = 0
        preprocessor = DataPreprocessor(batch_size=DEFAULT_BATCH_SIZE)
        # Use the batch generator to insert data in chunks.
        for i, batch in enumerate(preprocessor.batch_generator(documents)):
            if not batch:
                continue

            collection.insert_many(batch, ordered=False)
            total_inserted += len(batch)
            logger.info(f"  ... inserted batch {i + 1} ({total_inserted}/{len(documents)} documents)")

        logger.info(f"SILSO data load complete. Total inserted: {total_inserted}")

    except BulkWriteError as bwe:
        logger.error(f"Error during bulk insert: {bwe.details}")
    except Exception as e:
        logger.error(f"Error loading data to MongoDB: {e}")
