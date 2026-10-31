import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel

from space_weather_analysis.util.time_utils import UtcDatetime

# The target of a kind that makes one single request instead of one per dataset.
SINGLE_REQUEST_TARGET = "all"


@dataclass(frozen=True)
class PlannedRequest:
    """A metadata request the planner of a task kind lists for regular repetition; `sync` turns it into a stored task.

    Attributes:
        target: What the request fetches within its kind: a dataset ID, or a fixed name if the kind makes a single request.
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
    next_due_at: UtcDatetime
    last_attempt_at: UtcDatetime | None = None
    last_success_at: UtcDatetime | None = None
    consecutive_failures: int = 0
    last_error: str | None = None

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


def first_due_time(planned_request: PlannedRequest, key: str, interval: timedelta, now: datetime) -> datetime:
    """Places the first run of a new task.

    A kind with one single request runs at once, as nothing competes with it. The tasks of a kind with
    one request per dataset start at a fixed point within their interval: the offset comes from a hash
    of the key, so they spread evenly instead of all running at once, and keep their offset across restarts.

    Args:
        planned_request: The request the task repeats.
        key: The task key.
        interval: The time between two runs.
        now: The current time.

    Returns:
        The time of the first run, at most one interval after now.
    """
    if planned_request.target == SINGLE_REQUEST_TARGET:
        return now
    interval_seconds = max(int(interval.total_seconds()), 1)
    offset_seconds = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % interval_seconds
    return now + timedelta(seconds=offset_seconds)


@dataclass(frozen=True)
class TaskKind:
    """One kind of recurring metadata request and how to plan and run it.

    Attributes:
        name: The kind name stored in each task.
        interval: The time between two successful runs of a task.
        plan: Lists the requests the kind currently needs.
        run: Executes one task; raises on failure, so the worker schedules a retry.
    """

    name: str
    interval: timedelta
    plan: Callable[[], list[PlannedRequest]]
    run: Callable[[MetadataTask], None]
