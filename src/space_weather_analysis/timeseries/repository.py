import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from itertools import batched
from typing import Any

from pymongo.database import Database

from space_weather_analysis.timeseries.documents import DATA_FIELD, META_FIELD, TIME_FIELD, TimeSeriesDocument
from space_weather_analysis.timeseries.models import Granularity

logger = logging.getLogger(__name__)


class TimeSeriesRepository(ABC):
    """Stores the measured values of the datasets as time series documents, one collection per dataset.

    Pipelines write through this interface; time ranges are half-open, from start (inclusive) to stop (exclusive).
    """

    @abstractmethod
    def ensure_collection(self, collection: str, granularity: Granularity) -> None:
        """Creates the time series collection if it does not exist.

        Args:
            collection: The name of the collection.
            granularity: The unit closest to the spacing of the documents.
        """

    @abstractmethod
    def insert(self, collection: str, documents: Sequence[TimeSeriesDocument]) -> int:
        """Inserts documents.

        Args:
            collection: The name of the collection.
            documents: The documents to insert.

        Returns:
            The number of inserted documents.
        """

    @abstractmethod
    def replace_range(self, collection: str, start: datetime, stop: datetime, documents: Sequence[TimeSeriesDocument]) -> int:
        """Replaces the stored documents of a time range with the given documents.

        Args:
            collection: The name of the collection.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).
            documents: The new documents; their time stamps must lie inside the range.

        Returns:
            The number of inserted documents.
        """

    @abstractmethod
    def replace_all(self, collection: str, documents: Sequence[TimeSeriesDocument]) -> int:
        """Replaces every stored document of a collection with the given documents.

        Args:
            collection: The name of the collection.
            documents: The new documents.

        Returns:
            The number of inserted documents.
        """

    @abstractmethod
    def has_documents(self, collection: str, start: datetime, stop: datetime) -> bool:
        """Checks whether a time range holds at least one stored document.

        Args:
            collection: The name of the collection.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).

        Returns:
            True if a document has a time stamp inside the range.
        """

    @abstractmethod
    def count(self, collection: str, start: datetime, stop: datetime, data_values: Mapping[str, Any] | None = None) -> int:
        """Counts the stored documents of a time range.

        Args:
            collection: The name of the collection.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).
            data_values: The values that data fields must equal for a document to count, by field name; None counts every document.

        Returns:
            The number of matching documents.
        """


class MongoTimeSeriesRepository(TimeSeriesRepository):
    """Time series repository on MongoDB time series collections.

    Every method raises the pymongo error of a failed operation, so the caller can record the failure.
    Deletes by time range need MongoDB 7.0 or later on time series collections.
    """

    # Documents sent in one insert request
    BATCH_SIZE = 5000

    def __init__(self, db: Database) -> None:
        """Initializes the repository with a database connection.

        Args:
            db: The MongoDB database instance.
        """
        self.db = db

    def ensure_collection(self, collection: str, granularity: Granularity) -> None:
        """Creates the time series collection with the fields of the time series documents if it does not exist.

        Args:
            collection: The name of the collection.
            granularity: The unit closest to the spacing of the documents.
        """
        if self.db.list_collection_names(filter={"name": collection}):
            return
        logger.info("Creating time series collection %s with granularity %s", collection, granularity)
        self.db.create_collection(collection, timeseries={"timeField": TIME_FIELD, "metaField": META_FIELD, "granularity": str(granularity)})

    def insert(self, collection: str, documents: Sequence[TimeSeriesDocument]) -> int:
        """Inserts documents in batches of BATCH_SIZE.

        Args:
            collection: The name of the collection.
            documents: The documents to insert.

        Returns:
            The number of inserted documents.
        """
        inserted = 0
        for batch in batched(documents, self.BATCH_SIZE):
            # ordered=False inserts the other documents of a batch even if one fails
            self.db[collection].insert_many(batch, ordered=False)
            inserted += len(batch)
        logger.info("Inserted %d documents into %s", inserted, collection)
        return inserted

    def replace_range(self, collection: str, start: datetime, stop: datetime, documents: Sequence[TimeSeriesDocument]) -> int:
        """Deletes the stored documents of a time range, then inserts the given documents; the two steps are not atomic.

        Args:
            collection: The name of the collection.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).
            documents: The new documents; their time stamps must lie inside the range.

        Returns:
            The number of inserted documents.
        """
        deleted = self.db[collection].delete_many(_time_range_filter(start, stop))
        logger.info("Deleted %d documents of %s between %s and %s for replacement", deleted.deleted_count, collection, start, stop)
        return self.insert(collection, documents)

    def replace_all(self, collection: str, documents: Sequence[TimeSeriesDocument]) -> int:
        """Deletes every stored document of a collection, then inserts the given documents; the two steps are not atomic.

        Args:
            collection: The name of the collection.
            documents: The new documents.

        Returns:
            The number of inserted documents.
        """
        deleted = self.db[collection].delete_many({})
        logger.info("Deleted all %d documents of %s for replacement", deleted.deleted_count, collection)
        return self.insert(collection, documents)

    def has_documents(self, collection: str, start: datetime, stop: datetime) -> bool:
        """Checks whether a time range holds at least one stored document.

        Args:
            collection: The name of the collection.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).

        Returns:
            True if a document has a time stamp inside the range.
        """
        return self.db[collection].find_one(_time_range_filter(start, stop), projection={"_id": 1}) is not None

    def count(self, collection: str, start: datetime, stop: datetime, data_values: Mapping[str, Any] | None = None) -> int:
        """Counts the stored documents of a time range whose data fields equal the given values.

        Args:
            collection: The name of the collection.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).
            data_values: The values that data fields must equal for a document to count, by field name; None counts every document.

        Returns:
            The number of matching documents.
        """
        query = _time_range_filter(start, stop)
        for field_name, value in (data_values or {}).items():
            query[f"{DATA_FIELD}.{field_name}"] = value
        return self.db[collection].count_documents(query)


def _time_range_filter(start: datetime, stop: datetime) -> dict[str, Any]:
    """Builds the query filter for documents with a time stamp in [start, stop).

    Args:
        start: The start of the time range (inclusive).
        stop: The end of the time range (exclusive).

    Returns:
        The filter on the time field.
    """
    return {TIME_FIELD: {"$gte": start, "$lt": stop}}
