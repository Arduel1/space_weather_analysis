import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence
from urllib.parse import urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from catalog_collector.scheduling.models import MetadataTask, PlannedRequest
from catalog_collector.scheduling.repository import MetadataTaskRepository

logger = logging.getLogger(__name__)

# Due tasks read per round; a task whose host is still pausing waits for a later round.
DUE_SCAN_LIMIT = 100

# Longest error text kept in a task.
MAX_ERROR_LENGTH = 500


class WorkerSettings(BaseSettings):
    """Pacing of the metadata worker, read from environment variables `METADATA_WORKER_{field name}` in upper case.

    Attributes:
        host_pause_min_seconds: The shortest pause between two requests to the same host.
        host_pause_max_seconds: The longest pause; each pause is drawn at random between both.
        idle_sleep_seconds: The sleep when no task can run.
        interval_jitter: The share of the interval by which the next run is moved at random, so runs drift apart.
        failure_backoff_seconds: The retry delay after the first failure; it doubles per further failure up to the interval.
        plan_interval_seconds: The time between two comparisons of the planned requests with the stored tasks.
        hapi_mirror_interval_hours: The time between two reads of a HAPI mirror file.
        cdas_interval_hours: The time between two reads of the CDAS dataset list.
    """

    model_config = SettingsConfigDict(env_prefix="METADATA_WORKER_", frozen=True)

    host_pause_min_seconds: float = 30.0
    host_pause_max_seconds: float = 90.0
    idle_sleep_seconds: float = 30.0
    interval_jitter: float = 0.1
    failure_backoff_seconds: float = 900.0
    plan_interval_seconds: float = 3600.0
    hapi_mirror_interval_hours: float = 24.0
    cdas_interval_hours: float = 24.0

    @model_validator(mode="after")
    def _pause_range_ordered(self) -> "WorkerSettings":
        """Rejects a pause range whose minimum lies above its maximum."""
        if self.host_pause_min_seconds > self.host_pause_max_seconds:
            raise ValueError("host_pause_min_seconds must not exceed host_pause_max_seconds")
        return self


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
    plan: Callable[[], List[PlannedRequest]]
    run: Callable[[MetadataTask], None]


class TaskFailedError(Exception):
    """Raised by a task handler whose request did not deliver usable metadata.

    Attributes:
        retry_after_seconds: The wait the provider asked for before the next request, from its Retry-After header; None if it asked for none.
    """

    def __init__(self, message: str, retry_after_seconds: Optional[float] = None) -> None:
        """Initializes the error.

        Args:
            message: What failed.
            retry_after_seconds: The wait the provider asked for before the next request.
        """
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class MetadataWorker:
    """Runs the recurring metadata requests of all kinds one at a time, paced per host.

    Every kind is scheduled, paced and retried the same way; only the handler differs. Between
    two requests to the same host lies a random pause, so the requests form no regular pattern.
    """

    def __init__(
        self,
        repository: MetadataTaskRepository,
        kinds: Sequence[TaskKind],
        settings: WorkerSettings,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        rng: Optional[random.Random] = None,
    ) -> None:
        """Initializes the worker.

        Args:
            repository: The stored tasks.
            kinds: The kinds of request to plan and run.
            settings: The pacing.
            clock: Returns the current time in UTC.
            rng: The random source of pauses and jitter; a new unseeded one if None.
        """
        self.repository = repository
        self.kinds: Dict[str, TaskKind] = {kind.name: kind for kind in kinds}
        self.settings = settings
        self.clock = clock
        self.rng = rng or random.Random()
        self._host_ready_at: Dict[str, datetime] = {}
        self._next_plan_at: Optional[datetime] = None

    def run_forever(self, sleep: Callable[[float], None] = time.sleep) -> None:
        """Runs due tasks until the process ends, sleeping whenever none can run.

        Args:
            sleep: Waits the given number of seconds.
        """
        logger.info(f"Metadata worker started with kinds {sorted(self.kinds)}.")
        while True:
            if not self.run_once():
                sleep(self.settings.idle_sleep_seconds)

    def run_once(self) -> bool:
        """Plans if the plan interval has passed and runs the longest overdue task whose host is ready.

        Returns:
            True if a task ran.
        """
        now = self.clock()
        if self._next_plan_at is None or now >= self._next_plan_at:
            self._plan(now)
            self._next_plan_at = now + timedelta(seconds=self.settings.plan_interval_seconds)

        for task in self.repository.due(now, DUE_SCAN_LIMIT):
            kind = self.kinds.get(task.kind)
            host = _host(task.url)
            if kind is None or self._host_ready_at.get(host, now) > now:
                continue
            self._execute(task, kind, host)
            return True
        return False

    def _plan(self, now: datetime) -> None:
        """Compares the planned requests of every kind with the stored tasks.

        A kind whose planning fails keeps its stored tasks unchanged.

        Args:
            now: The current time.
        """
        for kind in self.kinds.values():
            try:
                self.repository.sync(kind.name, kind.plan(), kind.interval, now)
            except Exception as e:
                logger.error(f"Planning metadata tasks of kind '{kind.name}' failed; its tasks stay as stored: {e}", exc_info=True)

    def _execute(self, task: MetadataTask, kind: TaskKind, host: str) -> None:
        """Runs one task, records its outcome and starts the pause of its host, at least as long as a requested Retry-After.

        Args:
            task: The due task.
            kind: The kind of the task.
            host: The host the task requests.
        """
        started_at = self.clock()
        retry_after = timedelta(0)
        try:
            kind.run(task)
        except Exception as e:
            finished_at = self.clock()
            if isinstance(e, TaskFailedError) and e.retry_after_seconds:
                retry_after = timedelta(seconds=e.retry_after_seconds)
            backoff_seconds = self.settings.failure_backoff_seconds * 2**task.consecutive_failures
            # The provider's Retry-After is never shortened, not even by the interval cap of the backoff
            retry_at = finished_at + max(min(timedelta(seconds=backoff_seconds), kind.interval), retry_after)
            self.repository.record_failure(task.id, finished_at, retry_at, str(e)[:MAX_ERROR_LENGTH])
            logger.warning(f"Metadata task '{task.id}' failed ({task.consecutive_failures + 1} in a row); retry at {retry_at}: {e}", exc_info=True)
        else:
            finished_at = self.clock()
            jitter = 1.0 + self.rng.uniform(-self.settings.interval_jitter, self.settings.interval_jitter)
            next_due_at = finished_at + kind.interval * jitter
            self.repository.record_success(task.id, finished_at, next_due_at)
            logger.info(f"Metadata task '{task.id}' done in {(finished_at - started_at).total_seconds():.1f} s; next run at {next_due_at}.")
        pause = timedelta(seconds=self.rng.uniform(self.settings.host_pause_min_seconds, self.settings.host_pause_max_seconds))
        self._host_ready_at[host] = finished_at + max(pause, retry_after)


def _host(url: str) -> str:
    """Reads the host of a request URL in lower case.

    Args:
        url: The request URL.

    Returns:
        The host, or the whole URL if it names none.
    """
    return (urlsplit(url).hostname or url).lower()
