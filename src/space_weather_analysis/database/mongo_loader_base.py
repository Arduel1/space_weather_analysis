import logging
from abc import ABC
from typing import Any, Dict, List, Optional

from pymongo.database import Database
from pymongo.errors import BulkWriteError

from space_weather_analysis.util.data_preprocessing import batch_generator

logger = logging.getLogger(__name__)


class MongoLoaderBase(ABC):
    """
    Abstract Base Class providing common MongoDB loading functionality.

    Handles TimeSeries collection creation and batched data insertion
    for inheriting classes.
    """

    DEFAULT_BATCH_SIZE = 5000

    def __init__(self, db: Database):
        """
        Initializes the loader with a database connection.

        Args:
            db (Database): The MongoDB database instance.
        """
        self.db = db

    def _ensure_timeseries_collection(
        self,
        collection_name: str,
        time_field: str,
        meta_field: str,
        **timeseries_options: Any,
    ) -> None:
        """
        Helper method to create a MongoDB time series collection if it
        does not exist.

        Args:
            collection_name (str): The name of the collection.
            time_field (str): The name of the field containing the timestamp.
            meta_field (str): The name of the field containing metadata.
            **timeseries_options: Additional options for the timeseries.
        """
        if collection_name not in self.db.list_collection_names():
            logger.info(f"Creating new time series collection: {collection_name}")

            options: Dict[str, Any] = {"granularity": "seconds"}
            options.update(timeseries_options)

            self.db.create_collection(
                collection_name,
                timeseries={
                    "timeField": time_field,
                    "metaField": meta_field,
                    **options,
                },
            )
        else:
            logger.info(f"Collection {collection_name} already exists.")

    def _load_data(
        self,
        collection_name: str,
        documents: List[Dict[str, Any]],
        batch_size: Optional[int] = None,
        clear_collection: bool = False,
    ) -> None:
        """
        Handles the "Load" step by inserting documents in batches.

        Args:
            collection_name (str): The name of the collection to load into.
            documents (List[Dict[str, Any]]): A list of documents to insert.
            batch_size (Optional[int]): The number of documents per batch.
            clear_collection (bool): If True, clears ALL existing data
                                     in the collection before loading.
                                     Defaults to False (Append).
        """
        if not documents:
            logger.info(f"No documents to load for collection '{collection_name}'.")
            return

        effective_batch_size = batch_size or self.DEFAULT_BATCH_SIZE
        collection = self.db[collection_name]

        try:
            if clear_collection:
                logger.warning(f"Clearing ALL existing data in {collection_name} (clear_collection=True)...")
                collection.delete_many({})

            logger.info(
                f"Loading {len(documents)} documents into {collection_name} in batches of {effective_batch_size}..."
            )

            total_inserted = 0
            for i, batch in enumerate(batch_generator(documents, effective_batch_size), start=1):
                if not batch:
                    continue

                collection.insert_many(batch, ordered=False)
                total_inserted += len(batch)
                logger.info(f"  ... inserted batch {i} ({total_inserted}/{len(documents)} documents)")

            logger.info(f"Data load complete for {collection_name}. Total inserted: {total_inserted}")

        except BulkWriteError as bwe:
            logger.error(
                f"Error during bulk insert into {collection_name}: {bwe.details}",
                exc_info=True,
            )
        except Exception as e:
            logger.error(
                f"Error loading data to MongoDB collection {collection_name}: {e}",
                exc_info=True,
            )
