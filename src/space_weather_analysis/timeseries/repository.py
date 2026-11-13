import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Hashable, Mapping, Sequence
from datetime import UTC, datetime
from itertools import batched
from typing import Any, NamedTuple

import pandas as pd
from bson import ObjectId
from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

from space_weather_analysis.timeseries.documents import DATA_FIELD, META_FIELD, TIME_FIELD, TimeSeriesDocument
from space_weather_analysis.timeseries.models import Granularity, TimeBucket
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)


class RangeChanges(NamedTuple):
    """The writes a replacement of a time range needed.

    Attributes:
        inserted: The number of inserted documents.
        deleted: The number of deleted documents.
    """

    inserted: int
    deleted: int


class TimeSeriesRepository(ABC):
    """Stores the measured values of the datasets as time series documents, one collection per dataset.

    Pipelines write and dashboards read through this interface; time ranges are half-open, from start (inclusive) to stop (exclusive).
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
    def replace_range(self, collection: str, start: datetime, stop: datetime, documents: Sequence[TimeSeriesDocument]) -> RangeChanges:
        """Makes a time range hold exactly the given documents, writing only the documents that differ from the stored ones.

        Args:
            collection: The name of the collection.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).
            documents: The new documents; their time stamps must lie inside the range.

        Returns:
            The numbers of inserted and deleted documents.
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

    @abstractmethod
    def read_range(self, collection: str, start: datetime, stop: datetime, fields: Sequence[str] | None = None) -> pd.DataFrame:
        """Reads the data fields of the documents of a time range.

        Args:
            collection: The name of the collection.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).
            fields: The data fields to read; None reads every data field.

        Returns:
            One row per document, sorted by time, with an aware UTC DatetimeIndex named TIME_FIELD and one column per data field;
            a field missing in a document is NaN or None. Without matching documents the frame is empty and has the requested columns.
        """

    @abstractmethod
    def read_latest(
        self, collection: str, fields: Sequence[str] | None = None, data_values: Mapping[str, Any] | None = None
    ) -> tuple[datetime, dict[str, Any]] | None:
        """Reads the newest document.

        Args:
            collection: The name of the collection.
            fields: The data fields to read; None reads every data field.
            data_values: The values that data fields must equal for a document to be read, by field name; None reads the
                newest document of all.

        Returns:
            The aware UTC time stamp and the data fields of the newest matching document, without the fields it lacks; None
            if no document matches.
        """

    @abstractmethod
    def read_aggregated(self, collection: str, start: datetime, stop: datetime, fields: Sequence[str], bucket: TimeBucket) -> pd.DataFrame:
        """Reads the mean of numeric data fields per time bucket of a time range.

        Args:
            collection: The name of the collection.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).
            fields: The data fields to average; values that are no numbers are left out of the mean.
            bucket: The length of the time buckets, which start at the calendar boundaries in UTC.

        Returns:
            One row per bucket with documents, sorted by time, with the aware UTC bucket start as index named TIME_FIELD,
            one column per field and the column "count" with the number of documents in the bucket.
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
        logger.debug("Inserted %d documents into %s", inserted, collection)
        return inserted

    def replace_range(self, collection: str, start: datetime, stop: datetime, documents: Sequence[TimeSeriesDocument]) -> RangeChanges:
        """Compares the new with the stored documents of a time range and writes only the differences.

        A document whose time stamp, metadata and data equal a stored one is kept; a changed document counts as deleted
        and inserted. The new documents are inserted before the obsolete ones are deleted, so an interrupted call leaves
        surplus documents, which the next call for the range deletes, but never missing ones. The steps are not atomic.

        Args:
            collection: The name of the collection.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).
            documents: The new documents; their time stamps must lie inside the range.

        Returns:
            The numbers of inserted and deleted documents.
        """
        # IDs of the stored documents per content; several documents may share time stamp and content
        stored_ids: defaultdict[Hashable, list[ObjectId]] = defaultdict(list)
        for stored in self.db[collection].find(_time_range_filter(start, stop)):
            stored_ids[_content_key(stored)].append(stored["_id"])

        new_documents = []
        for document in documents:
            matching_ids = stored_ids.get(_content_key(document))
            if matching_ids:
                matching_ids.pop()
            else:
                new_documents.append(document)
        obsolete_ids = [document_id for ids in stored_ids.values() for document_id in ids]

        inserted = self.insert(collection, new_documents)
        for batch in batched(obsolete_ids, self.BATCH_SIZE):
            self.db[collection].delete_many({"_id": {"$in": list(batch)}})
        logger.info("Replaced %s between %s and %s: %d inserted, %d deleted", collection, start, stop, inserted, len(obsolete_ids))
        return RangeChanges(inserted, len(obsolete_ids))

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

    def read_range(self, collection: str, start: datetime, stop: datetime, fields: Sequence[str] | None = None) -> pd.DataFrame:
        """Reads the data fields of the documents of a time range, sorted by time in MongoDB.

        Args:
            collection: The name of the collection.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).
            fields: The data fields to read; None reads every data field.

        Returns:
            One row per document with an aware UTC DatetimeIndex named TIME_FIELD and one column per data field;
            a field missing in a document is NaN or None. Without matching documents the frame is empty and has the requested columns.
        """
        cursor = self.db[collection].find(_time_range_filter(start, stop), projection=_data_projection(fields), sort=[(TIME_FIELD, ASCENDING)])
        documents = list(cursor)
        logger.debug("Read %d documents of %s between %s and %s", len(documents), collection, start, stop)
        times = [document[TIME_FIELD] for document in documents]
        rows = [document.get(DATA_FIELD, {}) for document in documents]
        columns = list(fields) if fields is not None else None
        return pd.DataFrame(rows, index=_utc_index(times), columns=columns)

    def read_latest(
        self, collection: str, fields: Sequence[str] | None = None, data_values: Mapping[str, Any] | None = None
    ) -> tuple[datetime, dict[str, Any]] | None:
        """Reads the newest document.

        Args:
            collection: The name of the collection.
            fields: The data fields to read; None reads every data field.
            data_values: The values that data fields must equal for a document to be read, by field name; None reads the
                newest document of all.

        Returns:
            The aware UTC time stamp and the data fields of the newest matching document, without the fields it lacks; None
            if no document matches.
        """
        # The data fields of a document lie below DATA_FIELD, so a filter names them by their dotted path
        query = {f"{DATA_FIELD}.{field_name}": value for field_name, value in (data_values or {}).items()}
        document = self.db[collection].find_one(query, projection=_data_projection(fields), sort=[(TIME_FIELD, DESCENDING)])
        if document is None:
            return None
        timestamp: datetime = document[TIME_FIELD]
        # A client without tz_aware returns naive datetimes, which MongoDB stores as UTC
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC), dict(document.get(DATA_FIELD, {}))

    def read_aggregated(self, collection: str, start: datetime, stop: datetime, fields: Sequence[str], bucket: TimeBucket) -> pd.DataFrame:
        """Reads the mean of numeric data fields per time bucket, grouped by $dateTrunc in MongoDB.

        Args:
            collection: The name of the collection.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).
            fields: The data fields to average; $avg leaves values that are no numbers out of the mean.
            bucket: The length of the time buckets, which start at the calendar boundaries in UTC.

        Returns:
            One row per bucket with documents with the aware UTC bucket start as index named TIME_FIELD,
            one column per field and the column "count" with the number of documents in the bucket.
        """
        # Output names of $group may not contain dots, so each field gets a positional alias
        aliases = {f"field_{position}": field_name for position, field_name in enumerate(fields)}
        group: dict[str, Any] = {"_id": {"$dateTrunc": {"date": f"${TIME_FIELD}", "unit": str(bucket), "timezone": "UTC"}}, "count": {"$sum": 1}}
        for alias, field_name in aliases.items():
            group[alias] = {"$avg": f"${DATA_FIELD}.{field_name}"}
        pipeline = [{"$match": _time_range_filter(start, stop)}, {"$group": group}, {"$sort": {"_id": ASCENDING}}]
        buckets = list(self.db[collection].aggregate(pipeline))
        logger.debug("Aggregated %d %s buckets of %s between %s and %s", len(buckets), bucket, collection, start, stop)
        rows = [{field_name: result.get(alias) for alias, field_name in aliases.items()} | {"count": result["count"]} for result in buckets]
        frame = pd.DataFrame(rows, index=_utc_index([result["_id"] for result in buckets]), columns=[*fields, "count"])
        return frame.astype({"count": "int64"})


def _time_range_filter(start: datetime, stop: datetime) -> dict[str, Any]:
    """Builds the query filter for documents with a time stamp in [start, stop).

    Args:
        start: The start of the time range (inclusive).
        stop: The end of the time range (exclusive).

    Returns:
        The filter on the time field.
    """
    return {TIME_FIELD: {"$gte": start, "$lt": stop}}


def _content_key(document: Mapping[str, Any]) -> Hashable:
    """Builds a key that is equal for documents with equal time stamp, metadata and data, whether new or read from MongoDB.

    Args:
        document: A time series document.

    Returns:
        The time stamp and the sorted metadata and data fields, with every time cut to milliseconds as MongoDB stores it.
    """
    return (_stored_time(document[TIME_FIELD]), _sorted_fields(document.get(META_FIELD, {})), _sorted_fields(document.get(DATA_FIELD, {})))


def _sorted_fields(fields: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    """Lists the fields of a document part sorted by name, with times cut to milliseconds."""
    return tuple(sorted((name, _stored_time(value) if isinstance(value, datetime) else value) for name, value in fields.items()))


def _stored_time(time: datetime) -> datetime:
    """Returns a time as MongoDB stores it: in UTC and cut to whole milliseconds; a naive time counts as UTC."""
    aware_time = ensure_aware(time)
    return aware_time.replace(microsecond=aware_time.microsecond // 1000 * 1000)


def _data_projection(fields: Sequence[str] | None) -> dict[str, int]:
    """Builds the projection on the time field and the requested data fields.

    Args:
        fields: The data fields to read; None reads the whole data field.

    Returns:
        The projection without the document ID and the metadata field.
    """
    projection = {"_id": 0, TIME_FIELD: 1}
    if fields is None:
        projection[DATA_FIELD] = 1
    else:
        projection.update({f"{DATA_FIELD}.{field_name}": 1 for field_name in fields})
    return projection


def _utc_index(times: Sequence[datetime]) -> pd.DatetimeIndex:
    """Builds the aware UTC index of a read frame.

    Args:
        times: The time stamps read from MongoDB; naive time stamps count as UTC.

    Returns:
        The DatetimeIndex named TIME_FIELD.
    """
    return pd.DatetimeIndex(pd.to_datetime(list(times), utc=True), name=TIME_FIELD)
