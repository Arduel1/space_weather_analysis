from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from pymongo import ASCENDING, UpdateOne
from pymongo.database import Database

from catalog_collector.scheduling.models import MetadataTask, PlannedRequest, first_due_time, task_key
from space_weather_analysis.database.bulk import bulk_write_in_batches


class MetadataTaskRepository(ABC):
    """Stores the schedule and state of the recurring metadata requests."""

    @abstractmethod
    def sync(self, kind: str, planned_requests: Sequence[PlannedRequest], interval: timedelta, now: datetime) -> None:
        """Makes the stored tasks of a kind match the planned requests.

        New requests get a task whose first run first_due_time places; existing tasks keep their
        schedule and state; tasks no longer planned are deactivated, never deleted.

        Args:
            kind: The kind of request.
            planned_requests: The requests the kind currently plans.
            interval: The time between two successful runs.
            now: The current time.
        """

    @abstractmethod
    def due(self, now: datetime, kinds: Sequence[str], limit: int) -> list[MetadataTask]:
        """Reads the active tasks of the given kinds whose next run is due.

        Args:
            now: The current time.
            kinds: The kinds a worker can run; tasks of other kinds are left out, so they do not fill the limit.
            limit: The largest number of tasks returned.

        Returns:
            The due tasks, the longest overdue first.
        """

    @abstractmethod
    def get(self, key: str) -> MetadataTask | None:
        """Reads one task.

        Args:
            key: The task key.

        Returns:
            The task, or None if no task has this key.
        """

    @abstractmethod
    def record_success(self, key: str, finished_at: datetime, next_due_at: datetime) -> None:
        """Records a successful run and schedules the next one.

        Args:
            key: The task key.
            finished_at: When the run ended.
            next_due_at: The earliest time of the next run.
        """

    @abstractmethod
    def record_failure(self, key: str, finished_at: datetime, next_due_at: datetime, error: str) -> None:
        """Records a failed run and schedules the retry.

        Args:
            key: The task key.
            finished_at: When the run ended.
            next_due_at: The earliest time of the retry.
            error: The error description.
        """


class MongoMetadataTaskRepository(MetadataTaskRepository):
    """Metadata task repository on MongoDB, one document per task in 'metadata_tasks' with the task key as `_id`."""

    COLLECTION = "metadata_tasks"
    BATCH_SIZE = 1000

    def __init__(self, db: Database) -> None:
        """Initializes the repository; the index is created before the first write.

        Args:
            db: The MongoDB database instance.
        """
        self.collection = db[self.COLLECTION]
        self._index_ensured = False

    def _ensure_index(self) -> None:
        """Creates the index for the due query, once per instance."""
        if self._index_ensured:
            return
        self.collection.create_index([("active", ASCENDING), ("next_due_at", ASCENDING)])
        self._index_ensured = True

    def sync(self, kind: str, planned_requests: Sequence[PlannedRequest], interval: timedelta, now: datetime) -> None:
        """Makes the stored tasks of a kind match the planned requests in batches of BATCH_SIZE.

        Args:
            kind: The kind of request.
            planned_requests: The requests the kind currently plans.
            interval: The time between two successful runs.
            now: The current time.
        """
        self._ensure_index()
        interval_seconds = int(interval.total_seconds())
        operations = []
        planned_keys = []
        for planned_request in planned_requests:
            key = task_key(kind, planned_request.target)
            planned_keys.append(key)
            operations.append(
                UpdateOne(
                    {"_id": key},
                    {
                        "$set": {
                            "kind": kind,
                            "target": planned_request.target,
                            "url": planned_request.url,
                            "interval_seconds": interval_seconds,
                            "active": True,
                        },
                        "$setOnInsert": {"next_due_at": first_due_time(planned_request, key, interval, now), "consecutive_failures": 0},
                    },
                    upsert=True,
                )
            )
        bulk_write_in_batches(self.collection, operations, self.BATCH_SIZE)
        self.collection.update_many({"kind": kind, "_id": {"$nin": planned_keys}, "active": True}, {"$set": {"active": False}})

    def due(self, now: datetime, kinds: Sequence[str], limit: int) -> list[MetadataTask]:
        """Reads the active tasks of the given kinds whose next run is due.

        Args:
            now: The current time.
            kinds: The kinds a worker can run; tasks of other kinds are left out, so they do not fill the limit.
            limit: The largest number of tasks returned.

        Returns:
            The due tasks, the longest overdue first.
        """
        query = {"active": True, "kind": {"$in": list(kinds)}, "next_due_at": {"$lte": now}}
        documents = self.collection.find(query).sort("next_due_at", ASCENDING).limit(limit)
        return [MetadataTask.from_document(document) for document in documents]

    def get(self, key: str) -> MetadataTask | None:
        """Reads one task.

        Args:
            key: The task key.

        Returns:
            The task, or None if no task has this key.
        """
        document = self.collection.find_one({"_id": key})
        return MetadataTask.from_document(document) if document else None

    def record_success(self, key: str, finished_at: datetime, next_due_at: datetime) -> None:
        """Records a successful run and schedules the next one.

        Args:
            key: The task key.
            finished_at: When the run ended.
            next_due_at: The earliest time of the next run.
        """
        update: dict[str, Any] = {
            "$set": {"last_attempt_at": finished_at, "last_success_at": finished_at, "next_due_at": next_due_at, "consecutive_failures": 0},
            "$unset": {"last_error": ""},
        }
        self.collection.update_one({"_id": key}, update)

    def record_failure(self, key: str, finished_at: datetime, next_due_at: datetime, error: str) -> None:
        """Records a failed run and schedules the retry.

        Args:
            key: The task key.
            finished_at: When the run ended.
            next_due_at: The earliest time of the retry.
            error: The error description.
        """
        update: dict[str, Any] = {
            "$set": {"last_attempt_at": finished_at, "next_due_at": next_due_at, "last_error": error},
            "$inc": {"consecutive_failures": 1},
        }
        self.collection.update_one({"_id": key}, update)
