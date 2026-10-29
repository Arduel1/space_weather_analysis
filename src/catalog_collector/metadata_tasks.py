import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence

from pydantic import BaseModel, field_validator
from pymongo import ASCENDING, UpdateOne
from pymongo.database import Database

from space_weather_analysis.database.bulk import bulk_write_in_batches
from space_weather_analysis.util.time_utils import ensure_aware


@dataclass(frozen=True)
class TaskSpec:
    """One metadata request a task kind wants to be repeated.

    Attributes:
        target: What the request fetches within its kind, a dataset ID or a fixed name for single requests.
        url: The location requested; its host decides the pause before the next request to the same host.
    """

    target: str
    url: str


class MetadataTask(BaseModel):
    """The schedule and state of one recurring metadata request.

    Attributes:
        id: The task key `{kind}/{target}`, stored as `_id`.
        kind: The kind of request, which selects the handler.
        target: What the request fetches within its kind.
        url: The location requested.
        interval_seconds: The time between two successful runs.
        active: False if the planner no longer lists the task; inactive tasks are kept but not run.
        next_due_at: The earliest time of the next run.
        last_attempt_at: When the task last ran.
        last_success_at: When the task last succeeded.
        consecutive_failures: The failed runs since the last success.
        last_error: The error of the last failed run.
    """

    id: str
    kind: str
    target: str
    url: str
    interval_seconds: int
    active: bool = True
    next_due_at: datetime
    last_attempt_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None

    @field_validator("next_due_at", "last_attempt_at", "last_success_at")
    @classmethod
    def _times_in_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        """Reads naive times as UTC, as MongoDB returns stored times without time zone."""
        return ensure_aware(value) if value is not None else None

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "MetadataTask":
        """Builds a task from its MongoDB document.

        Args:
            document: The stored document with the task key in `_id`.

        Returns:
            The task.

        Raises:
            pydantic.ValidationError: If the document does not match the schema.
        """
        fields = {key: value for key, value in document.items() if key != "_id"}
        return cls.model_validate({"id": document["_id"], **fields})


def task_key(kind: str, target: str) -> str:
    """Builds the key of a task.

    Args:
        kind: The kind of request.
        target: What the request fetches within its kind.

    Returns:
        The key `{kind}/{target}`.
    """
    return f"{kind}/{target}"


def first_due_time(key: str, interval: timedelta, now: datetime) -> datetime:
    """Places the first run of a new task at a fixed point within its interval.

    The offset comes from a hash of the key, so the tasks of one kind spread evenly over the
    interval instead of all running at once, and a task keeps its offset across restarts.

    Args:
        key: The task key.
        interval: The time between two runs.
        now: The current time.

    Returns:
        The time of the first run, at most one interval after now.
    """
    interval_seconds = max(int(interval.total_seconds()), 1)
    offset_seconds = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % interval_seconds
    return now + timedelta(seconds=offset_seconds)


class MetadataTaskRepository(ABC):
    """Stores the schedule and state of the recurring metadata requests."""

    @abstractmethod
    def sync(self, kind: str, specs: Sequence[TaskSpec], interval: timedelta, now: datetime) -> None:
        """Makes the stored tasks of a kind match the planned requests.

        New requests get a task with its first run spread over the interval; existing tasks keep
        their schedule and state; tasks no longer planned are deactivated, never deleted.

        Args:
            kind: The kind of request.
            specs: The planned requests of the kind.
            interval: The time between two successful runs.
            now: The current time.
        """

    @abstractmethod
    def due(self, now: datetime, limit: int) -> List[MetadataTask]:
        """Reads the active tasks whose next run is due.

        Args:
            now: The current time.
            limit: The largest number of tasks returned.

        Returns:
            The due tasks, the longest overdue first.
        """

    @abstractmethod
    def get(self, key: str) -> Optional[MetadataTask]:
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

    def sync(self, kind: str, specs: Sequence[TaskSpec], interval: timedelta, now: datetime) -> None:
        """Makes the stored tasks of a kind match the planned requests in batches of BATCH_SIZE.

        Args:
            kind: The kind of request.
            specs: The planned requests of the kind.
            interval: The time between two successful runs.
            now: The current time.
        """
        self._ensure_index()
        interval_seconds = int(interval.total_seconds())
        operations = []
        planned_keys = []
        for spec in specs:
            key = task_key(kind, spec.target)
            planned_keys.append(key)
            operations.append(
                UpdateOne(
                    {"_id": key},
                    {
                        "$set": {"kind": kind, "target": spec.target, "url": spec.url, "interval_seconds": interval_seconds, "active": True},
                        "$setOnInsert": {"next_due_at": first_due_time(key, interval, now), "consecutive_failures": 0},
                    },
                    upsert=True,
                )
            )
        bulk_write_in_batches(self.collection, operations, self.BATCH_SIZE)
        self.collection.update_many({"kind": kind, "_id": {"$nin": planned_keys}, "active": True}, {"$set": {"active": False}})

    def due(self, now: datetime, limit: int) -> List[MetadataTask]:
        """Reads the active tasks whose next run is due.

        Args:
            now: The current time.
            limit: The largest number of tasks returned.

        Returns:
            The due tasks, the longest overdue first.
        """
        documents = self.collection.find({"active": True, "next_due_at": {"$lte": now}}).sort("next_due_at", ASCENDING).limit(limit)
        return [MetadataTask.from_document(document) for document in documents]

    def get(self, key: str) -> Optional[MetadataTask]:
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
        update: Dict[str, Any] = {
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
        update: Dict[str, Any] = {
            "$set": {"last_attempt_at": finished_at, "next_due_at": next_due_at, "last_error": error},
            "$inc": {"consecutive_failures": 1},
        }
        self.collection.update_one({"_id": key}, update)
