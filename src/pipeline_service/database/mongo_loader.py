import logging
from datetime import datetime
from typing import Any

from pymongo.database import Database

from pipeline_service.util.batching import batch_generator

logger = logging.getLogger(__name__)


class MongoLoader:
    """Mixin giving pipelines the creation of time series collections and the batched insertion of documents.

    Every method raises the pymongo error of a failed write, so the caller can record the failure.
    """

    DEFAULT_BATCH_SIZE = 5000

    def __init__(self, db: Database) -> None:
        """Initializes the loader with a database connection.

        Args:
            db: The MongoDB database instance.
        """
        self.db = db

    def _ensure_timeseries_collection(self, collection_name: str, time_field: str, meta_field: str, **timeseries_options: Any) -> None:
        """Creates a MongoDB time series collection if it does not exist.

        Args:
            collection_name: The name of the collection.
            time_field: The name of the field containing the timestamp.
            meta_field: The name of the field containing metadata.
            **timeseries_options: Further time series options; the granularity is "seconds" unless given.

        Raises:
            pymongo.errors.PyMongoError: If the collection cannot be listed or created.
        """
        if collection_name in self.db.list_collection_names():
            return
        logger.info("Creating time series collection %s", collection_name)
        options: dict[str, Any] = {"granularity": "seconds", **timeseries_options}
        self.db.create_collection(collection_name, timeseries={"timeField": time_field, "metaField": meta_field, **options})

    def _load_data(self, collection_name: str, documents: list[dict[str, Any]], *, clear_collection: bool = False) -> None:
        """Inserts documents in batches of DEFAULT_BATCH_SIZE.

        Args:
            collection_name: The name of the collection to load into.
            documents: The documents to insert.
            clear_collection: Whether to delete every stored document of the collection first.

        Raises:
            pymongo.errors.PyMongoError: If the delete or an insert fails.
        """
        if not documents:
            logger.info("No documents to load into %s.", collection_name)
            return

        collection = self.db[collection_name]
        if clear_collection:
            logger.info("Deleting every stored document of %s before the load.", collection_name)
            collection.delete_many({})

        total_inserted = 0
        for batch in batch_generator(documents, self.DEFAULT_BATCH_SIZE):
            # ordered=False inserts the other documents of a batch even if one fails
            collection.insert_many(batch, ordered=False)
            total_inserted += len(batch)
        logger.info("Inserted %d documents into %s.", total_inserted, collection_name)

    def _replace_time_range(self, collection_name: str, time_field: str, start: datetime, stop: datetime, documents: list[dict[str, Any]]) -> None:
        """Replaces the stored documents of a time range with the given documents.

        Deletes every document with start <= time_field < stop, then inserts the documents. The two steps
        are not atomic. Time series collections accept this delete filter from MongoDB 7.0 on.

        Args:
            collection_name: The name of the collection.
            time_field: The name of the field containing the timestamp.
            start: Start of the time range (inclusive).
            stop: End of the time range (exclusive).
            documents: The new documents; their times must lie inside the range.

        Raises:
            pymongo.errors.PyMongoError: If the delete or an insert fails.
        """
        deleted = self.db[collection_name].delete_many({time_field: {"$gte": start, "$lt": stop}})
        logger.info("Deleted %d documents of %s between %s and %s for replacement.", deleted.deleted_count, collection_name, start, stop)
        self._load_data(collection_name, documents)
