import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from pymongo.database import Database
from pymongo.errors import BulkWriteError, OperationFailure

from pipeline_service.util.batching import batch_generator

logger = logging.getLogger(__name__)


class MongoLoader:
    """Abstract Base Class providing common MongoDB loading functionality.

    Handles TimeSeries collection creation and batched data insertion
    for inheriting classes. This class is designed to be mixed into
    pipeline classes that require database write access.
    """

    DEFAULT_BATCH_SIZE = 5000

    def __init__(self, db: Database) -> None:
        """Initializes the loader with a database connection.

        Args:
            db: The MongoDB database instance.
        """
        self.db = db

    def _ensure_timeseries_collection(
        self,
        collection_name: str,
        time_field: str,
        meta_field: str,
        **timeseries_options: Any,
    ) -> None:
        """Creates a MongoDB time series collection if it does not exist.

        Args:
            collection_name: The name of the collection.
            time_field: The name of the field containing the timestamp.
            meta_field: The name of the field containing metadata.
            **timeseries_options: Additional options for the timeseries.
        """
        try:
            # Check existing collections to avoid error on recreation attempt
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

        except OperationFailure as e:
            logger.error(
                f"MongoDB operation failed while creating collection '{collection_name}': {e}",
                exc_info=True,
            )
        except Exception as e:
            logger.error(
                f"Unexpected error ensuring collection '{collection_name}': {e}",
                exc_info=True,
            )

    def _load_data(
        self,
        collection_name: str,
        documents: List[Dict[str, Any]],
        batch_size: Optional[int] = None,
        clear_collection: bool = False,
    ) -> None:
        """Handles the "Load" step by inserting documents in batches.

        Args:
            collection_name: The name of the collection to load into.
            documents: A list of documents to insert.
            batch_size: The number of documents per batch.
            clear_collection: If True, clears ALL existing data
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

            logger.info(f"Loading {len(documents)} documents into {collection_name} in batches of {effective_batch_size}...")

            total_inserted = 0
            # Use utility generator for memory-efficient processing
            for i, batch in enumerate(batch_generator(documents, effective_batch_size), start=1):
                # ordered=False allows continuing insertion even if one document fails
                collection.insert_many(batch, ordered=False)
                total_inserted += len(batch)
                logger.info(f"Inserted batch {i} ({total_inserted}/{len(documents)} documents)")

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

    def _replace_time_range(
        self,
        collection_name: str,
        time_field: str,
        start: datetime,
        stop: datetime,
        documents: List[Dict[str, Any]],
    ) -> None:
        """Replaces the stored documents of a time range with the given documents.

        Deletes every document with start <= time_field < stop, then inserts the
        documents via _load_data. The two steps are not atomic. Time series
        collections accept this delete filter from MongoDB 7.0 on.

        Args:
            collection_name: The name of the collection.
            time_field: The name of the field containing the timestamp.
            start: Start of the time range (inclusive).
            stop: End of the time range (exclusive).
            documents: The new documents; their times must lie inside the range.
        """
        try:
            deleted = self.db[collection_name].delete_many({time_field: {"$gte": start, "$lt": stop}})
            logger.info(f"Deleted {deleted.deleted_count} documents from {collection_name} between {start} and {stop} for replacement.")
        except Exception as e:
            # Without the delete the insert would duplicate the kept documents
            logger.error(f"Error deleting documents from {collection_name} between {start} and {stop}: {e}", exc_info=True)
            return

        self._load_data(collection_name, documents)
