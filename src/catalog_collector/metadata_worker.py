import logging
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence
from urllib.parse import urlsplit

from pydantic import ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pymongo.database import Database

from catalog_collector.availability import MIRROR_BASE_URL, MIRROR_DATASET_TABLE_PATH, AvailabilityRefresher
from catalog_collector.metadata_refresh import MIRROR_INFOS_PATH, MetadataRefresher
from catalog_collector.metadata_tasks import MetadataTask, MetadataTaskRepository, MongoMetadataTaskRepository, TaskSpec
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.config.mongo import MongoSettings
from space_weather_analysis.database.connection import MongoConnection
from space_weather_analysis.logging.config import setup_logging
from space_weather_analysis.metadata.repository import MongoMetadataRepository

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
    """

    model_config = SettingsConfigDict(env_prefix="METADATA_WORKER_", frozen=True)

    host_pause_min_seconds: float = 30.0
    host_pause_max_seconds: float = 90.0
    idle_sleep_seconds: float = 30.0
    interval_jitter: float = 0.1
    failure_backoff_seconds: float = 900.0
    plan_interval_seconds: float = 3600.0
    hapi_mirror_interval_hours: float = 24.0

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
    plan: Callable[[], List[TaskSpec]]
    run: Callable[[MetadataTask], None]


class TaskFailedError(Exception):
    """Raised by a task handler whose request did not deliver usable metadata."""


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
        """Runs one task, records its outcome and starts the pause of its host.

        Args:
            task: The due task.
            kind: The kind of the task.
            host: The host the task requests.
        """
        started_at = self.clock()
        try:
            kind.run(task)
        except Exception as e:
            finished_at = self.clock()
            backoff_seconds = self.settings.failure_backoff_seconds * 2**task.consecutive_failures
            retry_at = finished_at + min(timedelta(seconds=backoff_seconds), kind.interval)
            self.repository.record_failure(task.id, finished_at, retry_at, str(e)[:MAX_ERROR_LENGTH])
            logger.warning(f"Metadata task '{task.id}' failed ({task.consecutive_failures + 1} in a row); retry at {retry_at}: {e}", exc_info=True)
        else:
            finished_at = self.clock()
            jitter = 1.0 + self.rng.uniform(-self.settings.interval_jitter, self.settings.interval_jitter)
            next_due_at = finished_at + kind.interval * jitter
            self.repository.record_success(task.id, finished_at, next_due_at)
            logger.info(f"Metadata task '{task.id}' done in {(finished_at - started_at).total_seconds():.1f} s; next run at {next_due_at}.")
        pause = self.rng.uniform(self.settings.host_pause_min_seconds, self.settings.host_pause_max_seconds)
        self._host_ready_at[host] = finished_at + timedelta(seconds=pause)


def _host(url: str) -> str:
    """Reads the host of a request URL in lower case.

    Args:
        url: The request URL.

    Returns:
        The host, or the whole URL if it names none.
    """
    return (urlsplit(url).hostname or url).lower()


def hapi_mirror_kinds(db: Database, settings: WorkerSettings, mirror_base_url: str = MIRROR_BASE_URL) -> List[TaskKind]:
    """Builds the task kinds that read the nightly files of the HAPI metadata mirror.

    Args:
        db: The MongoDB database instance.
        settings: The pacing, which sets the interval.
        mirror_base_url: The base URL of the mirror files.

    Returns:
        One kind refreshing the HAPI metadata and one refreshing the availability, each with a single task.
    """
    catalog_repository = MongoCatalogRepository(db)
    metadata_repository = MongoMetadataRepository(db)
    base_url = mirror_base_url.rstrip("/")
    interval = timedelta(hours=settings.hapi_mirror_interval_hours)

    def refresh_metadata(task: MetadataTask) -> None:
        """Refreshes the HAPI metadata from the mirror's full file."""
        if MetadataRefresher(catalog_repository, metadata_repository, base_url).refresh() is None:
            raise TaskFailedError("HAPI mirror file could not be read")

    def refresh_availability(task: MetadataTask) -> None:
        """Refreshes the availability of the HAPI sources from the mirror's catalog and dataset table."""
        if AvailabilityRefresher(catalog_repository, metadata_repository, base_url).refresh() is None:
            raise TaskFailedError("HAPI mirror catalog or dataset table could not be read")

    return [
        TaskKind("hapi_mirror_metadata", interval, lambda: [TaskSpec("all", f"{base_url}/{MIRROR_INFOS_PATH}")], refresh_metadata),
        TaskKind("hapi_mirror_availability", interval, lambda: [TaskSpec("all", f"{base_url}/{MIRROR_DATASET_TABLE_PATH}")], refresh_availability),
    ]


def main() -> None:
    """Runs the metadata worker as a long-lived background process.

    Invalid settings or a missing database connection exit with status 1; failing tasks are retried and never end the process.
    """
    setup_logging(service_name="MetadataWorker")

    try:
        mongo_settings = MongoSettings()
        worker_settings = WorkerSettings()
    except ValidationError as e:
        logger.critical(f"Invalid settings, check the MONGO_* and METADATA_WORKER_* environment variables: {format_settings_error(e)}. Exiting.")
        sys.exit(1)

    connection = MongoConnection(mongo_settings)
    db = connection.get_database()
    if db is None:
        logger.critical("Failed to obtain database connection. Exiting.")
        sys.exit(1)

    try:
        MetadataWorker(MongoMetadataTaskRepository(db), hapi_mirror_kinds(db, worker_settings), worker_settings).run_forever()
    finally:
        connection.close()


if __name__ == "__main__":
    main()
