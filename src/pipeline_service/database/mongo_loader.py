import logging
from datetime import datetime
from itertools import batched
from typing import Any

from pymongo.database import Database

from pipeline_service.util.documents import META_FIELD, TIME_FIELD, DocumentDict

logger = logging.getLogger(__name__)


class MongoLoader:
    """Mixin giving pipelines the creation of time series collections and the batched insertion of documents.

    Every method raises the pymongo error of a failed write, so the caller can record the failure.
    """

    # Documents sent in one insert request
    BATCH_SIZE = 5000

    def __init__(self, db: Database) -> None:
        """Initializes the loader with a database connection.

        Args:
            db: The MongoDB database instance.
        """
        self.db = db

    def _ensure_timeseries_collection(self, collection_name: str, **timeseries_options: Any) -> None:
        """Creates a MongoDB time series collection with the fields of the time series documents if it does not exist.

        Args:
            collection_name: The name of the collection.
            **timeseries_options: Further time series options; the granularity is "seconds" unless given.

        Raises:
            pymongo.errors.PyMongoError: If the collection cannot be listed or created.
        """
        if self.db.list_collection_names(filter={"name": collection_name}):
            return
        logger.info("Creating time series collection %s", collection_name)
        options: dict[str, Any] = {"granularity": "seconds", **timeseries_options}
        self.db.create_collection(collection_name, timeseries={"timeField": TIME_FIELD, "metaField": META_FIELD, **options})

    def _load_data(self, collection_name: str, documents: list[DocumentDict], *, clear_collection: bool = False) -> None:
        """Inserts documents in batches of BATCH_SIZE.

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
        for batch in batched(documents, self.BATCH_SIZE):
            # ordered=False inserts the other documents of a batch even if one fails
            collection.insert_many(batch, ordered=False)
            total_inserted += len(batch)
        logger.info("Inserted %d documents into %s.", total_inserted, collection_name)

    def _replace_time_range(self, collection_name: str, start: datetime, stop: datetime, documents: list[DocumentDict]) -> None:
        """Replaces the stored documents of a time range with the given documents.

        Deletes every document with a time stamp in [start, stop), then inserts the documents. The two steps
        are not atomic. Time series collections accept this delete filter from MongoDB 7.0 on.

        Args:
            collection_name: The name of the collection.
            start: Start of the time range (inclusive).
            stop: End of the time range (exclusive).
            documents: The new documents; their times must lie inside the range.

        Raises:
            pymongo.errors.PyMongoError: If the delete or an insert fails.
        """
        deleted = self.db[collection_name].delete_many({TIME_FIELD: {"$gte": start, "$lt": stop}})
        logger.info("Deleted %d documents of %s between %s and %s for replacement.", deleted.deleted_count, collection_name, start, stop)
        self._load_data(collection_name, documents)
