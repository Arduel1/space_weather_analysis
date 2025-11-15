import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import pandas as pd
from pymongo.database import Database
from pymongo.errors import BulkWriteError

from space_weather_analysis.util.data_preprocessing import batch_generator

logger = logging.getLogger(__name__)


class BaseETLProcessor(ABC):
    """
    Abstract Base Class for an ETL processor.

    This class provides a standardized structure for all ETL processes.
    It handles the "Load" step, including time series collection setup
    and batched data insertion, which are common to all data sources.

    A child class MUST implement:
    - _extract(): To fetch raw data (e.g., from a file).
    - _transform(): To clean and structure the data.
    - run(): The main orchestration method.
    """

    def __init__(self, db: Database):
        """
        Initializes the processor with a database connection.

        Args:
            db (Database): The MongoDB database instance.
        """
        self.db = db

    @abstractmethod
    def _extract(self) -> Optional[pd.DataFrame]:
        """
        Abstract method for the "Extract" step.
        This should read data from a source (file, API, etc.).

        Returns:
            Optional[pd.DataFrame]: A DataFrame containing the raw data,
                                    or None if extraction fails.
        """
        pass

    @abstractmethod
    def _transform(self, raw_data: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
        """
        Abstract method for the "Transform" step.
        This should clean and format the raw data into a list of documents.

        Args:
            raw_data (Optional[pd.DataFrame]): The raw data from _extract().

        Returns:
            List[Dict[str, Any]]: A list of formatted dictionaries (documents)
                                  for MongoDB.
        """
        pass

    def _ensure_timeseries_collection(
        self,
        collection_name: str,
        time_field: str,
        meta_field: str,
        **timeseries_options,
    ):
        """
        Helper method to create a MongoDB time series collection if it
        does not exist.

        Args:
            collection_name (str): The name of the collection.
            time_field (str): The name of the field containing the timestamp.
            meta_field (str): The name of the field containing metadata.
            **timeseries_options: Additional options (e.g., bucketMaxSpanSeconds).
        """
        if collection_name not in self.db.list_collection_names():
            logger.info(f"Creating new time series collection: {collection_name}")
            self.db.create_collection(
                collection_name,
                timeseries={
                    "timeField": time_field,
                    "metaField": meta_field,
                    **timeseries_options,
                },
            )
        else:
            logger.info(f"Collection {collection_name} already exists.")

    def _load_data(
        self,
        collection_name: str,
        documents: List[Dict[str, Any]],
        batch_size: int = 5000,
    ):
        """
        Handles the "Load" step by inserting documents in batches.

        This method is idempotent: it clears all existing data in the
        collection before inserting the new data.

        Args:
            collection_name (str): The name of the collection to load data into.
            documents (List[Dict[str, Any]]): A list of dictionaries to insert.
            batch_size (int): The number of documents per batch.
        """
        if not documents:
            logger.info("No documents to load.")
            return

        collection = self.db[collection_name]

        try:
            # Clear the collection to ensure the ETL process is idempotent
            logger.info(f"Clearing existing data in {collection_name}...")
            collection.delete_many({})

            logger.info(f"Loading {len(documents)} documents in batches of {batch_size}...")

            total_inserted = 0
            # Use the refactored batch_generator function
            for i, batch in enumerate(batch_generator(documents, batch_size), start=1):
                if not batch:
                    continue

                collection.insert_many(batch, ordered=False)
                total_inserted += len(batch)
                logger.info(f"  ... inserted batch {i} ({total_inserted}/{len(documents)} documents)")

            logger.info(f"Data load complete. Total inserted: {total_inserted}")

        except BulkWriteError as bwe:
            logger.error(f"Error during bulk insert: {bwe.details}", exc_info=True)
        except Exception as e:
            logger.error(f"Error loading data to MongoDB: {e}", exc_info=True)

    @abstractmethod
    def run(self):
        """
        Main orchestration method for the ETL process.
        This method should call _extract, _transform, and _load.
        """
        pass
