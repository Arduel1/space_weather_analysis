from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo.database import Database

from space_weather_analysis.coverage.models import CoverageInterval, StoredCoverage
from space_weather_analysis.util.time_utils import ensure_aware


class CoverageRepository(ABC):
    """Computes and stores which time ranges of each dataset are stored in the database.

    Pipelines refresh the coverage after ingesting; the read layer reads it to know what can be queried and compared.
    """

    @abstractmethod
    def compute(self, dataset_id: str, collection: str, time_field: str = "timestamp") -> StoredCoverage:
        """Computes the coverage of a dataset from its stored documents without saving it.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents of the dataset.
            time_field: The document field holding the time of each document.

        Returns:
            The coverage; without documents or collection it has a document count of 0 and no intervals.
        """

    @abstractmethod
    def save(self, coverage: StoredCoverage) -> None:
        """Inserts or replaces the coverage of one dataset.

        Args:
            coverage: The coverage to store.
        """

    @abstractmethod
    def get(self, dataset_id: str) -> StoredCoverage | None:
        """Reads the coverage of one dataset.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The coverage, or None if none is stored.
        """

    @abstractmethod
    def list_all(self) -> list[StoredCoverage]:
        """Reads the coverage of every dataset.

        Returns:
            The coverages, sorted by dataset ID.
        """

    def refresh(self, dataset_id: str, collection: str, time_field: str = "timestamp") -> StoredCoverage:
        """Computes the coverage of a dataset from its stored documents and saves it.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents of the dataset.
            time_field: The document field holding the time of each document.

        Returns:
            The saved coverage.
        """
        coverage = self.compute(dataset_id, collection, time_field)
        self.save(coverage)
        return coverage


class MongoCoverageRepository(CoverageRepository):
    """Coverage repository on MongoDB; each coverage is one document in 'data_coverage' with the dataset ID as `_id`."""

    COLLECTION = "data_coverage"

    # Bucket sizes in hours that divide a day, so buckets stay aligned to UTC midnight; one hour is the finest resolution.
    # The smallest size not below the typical spacing of the documents is used, as a bucket smaller than the spacing
    # would show gaps between regular records (daily or 3-hourly data).
    BUCKET_HOURS = (1, 2, 3, 6, 12, 24)

    def __init__(self, db: Database) -> None:
        """Initializes the repository; the index is created before the first write.

        Args:
            db: The MongoDB database instance.
        """
        self.db = db
        self.coverages = db[self.COLLECTION]
        self._indexes_ensured = False

    def _ensure_indexes(self) -> None:
        """Creates the index for lookups by collection, once per instance."""
        if self._indexes_ensured:
            return
        self.coverages.create_index("collection")
        self._indexes_ensured = True

    def compute(self, dataset_id: str, collection: str, time_field: str = "timestamp") -> StoredCoverage:
        """Computes the coverage of a dataset with one aggregation over its stored documents.

        A first aggregation yields count, earliest and latest time, from which the typical spacing of the
        documents selects the bucket size. A second aggregation groups the documents into UTC buckets of
        that size; consecutive buckets are merged into intervals here, as there are far fewer buckets than documents.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents of the dataset.
            time_field: The document field holding the time of each document.

        Returns:
            The coverage; without documents or collection it has a document count of 0 and no intervals.
        """
        time_path = f"${time_field}"
        # Documents without a date in the time field belong to no bucket
        dated_documents: dict[str, Any] = {"$match": {time_field: {"$type": "date"}}}
        totals_pipeline: list[dict[str, Any]] = [
            dated_documents,
            {"$group": {"_id": None, "document_count": {"$sum": 1}, "first_timestamp": {"$min": time_path}, "last_timestamp": {"$max": time_path}}},
        ]
        totals = next(self.db[collection].aggregate(totals_pipeline), None)
        bucket_hours = select_bucket_hours(typical_spacing(totals), self.BUCKET_HOURS)

        bucket_pipeline: list[dict[str, Any]] = [
            dated_documents,
            {"$group": {"_id": {"$dateTrunc": {"date": time_path, "unit": "hour", "binSize": bucket_hours, "timezone": "UTC"}}}},
            {"$sort": {"_id": 1}},
        ]
        bucket_starts = [ensure_aware(bucket["_id"]) for bucket in self.db[collection].aggregate(bucket_pipeline)]

        return StoredCoverage(
            dataset_id=dataset_id,
            collection=collection,
            first_timestamp=totals["first_timestamp"] if totals else None,
            last_timestamp=totals["last_timestamp"] if totals else None,
            document_count=totals["document_count"] if totals else 0,
            intervals=merge_consecutive_buckets(bucket_starts, timedelta(hours=bucket_hours)),
            bucket=f"PT{bucket_hours}H",
            updated_at=datetime.now(UTC),
        )

    def save(self, coverage: StoredCoverage) -> None:
        """Inserts or replaces the coverage of one dataset.

        Args:
            coverage: The coverage to store.
        """
        self._ensure_indexes()
        self.coverages.replace_one({"_id": coverage.dataset_id}, coverage.to_document(), upsert=True)

    def get(self, dataset_id: str) -> StoredCoverage | None:
        """Reads the coverage of one dataset.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The coverage, or None if none is stored.
        """
        document = self.coverages.find_one({"_id": dataset_id})
        return StoredCoverage.from_document(document) if document else None

    def list_all(self) -> list[StoredCoverage]:
        """Reads the coverage of every dataset.

        Returns:
            The coverages, sorted by dataset ID.
        """
        return [StoredCoverage.from_document(document) for document in self.coverages.find({}, sort=[("_id", 1)])]


def typical_spacing(totals: dict[str, Any] | None) -> timedelta | None:
    """Estimates the typical time between documents as their mean spacing.

    Args:
        totals: Count, earliest and latest time of the documents, or None without documents.

    Returns:
        The mean spacing, or None with fewer than two documents.
    """
    if not totals or totals["document_count"] < 2:
        return None
    return (totals["last_timestamp"] - totals["first_timestamp"]) / (totals["document_count"] - 1)


def select_bucket_hours(spacing: timedelta | None, bucket_hours: Sequence[int]) -> int:
    """Selects the smallest bucket size not below the spacing of the documents.

    Args:
        spacing: The typical time between documents, or None if unknown.
        bucket_hours: The allowed bucket sizes in hours, ascending.

    Returns:
        The bucket size in hours; the smallest one if the spacing is unknown, the largest one for sparser data.
    """
    if spacing is None:
        return bucket_hours[0]
    for hours in bucket_hours:
        if timedelta(hours=hours) >= spacing:
            return hours
    return bucket_hours[-1]


def merge_consecutive_buckets(bucket_starts: list[datetime], bucket_duration: timedelta) -> list[CoverageInterval]:
    """Merges time buckets that follow each other without gap into intervals.

    Args:
        bucket_starts: The start of each bucket holding documents, sorted ascending and without duplicates.
        bucket_duration: The length of each bucket.

    Returns:
        The half-open intervals from the start of the first to the end of the last bucket of each run.
    """
    intervals: list[CoverageInterval] = []
    for bucket_start in bucket_starts:
        bucket_end = bucket_start + bucket_duration
        if intervals and intervals[-1].end == bucket_start:
            intervals[-1].end = bucket_end
        else:
            intervals.append(CoverageInterval(start=bucket_start, end=bucket_end))
    return intervals
