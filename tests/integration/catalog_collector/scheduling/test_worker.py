import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from pymongo.database import Database

from catalog_collector.scheduling.models import MetadataTask, PlannedRequest, TaskKind
from catalog_collector.scheduling.repository import MongoMetadataTaskRepository
from catalog_collector.scheduling.worker import MetadataWorker, WorkerSettings
from catalog_collector.worker_tasks.data_catalog import hapi_availability_kind
from catalog_collector.worker_tasks.metadata_catalog import hapi_metadata_kind
from catalog_collector.worker_tasks.settings import TaskIntervalSettings
from space_weather_analysis.util.error_handling import DataRetrievalError

START = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
INTERVAL = timedelta(days=1)
SETTINGS = WorkerSettings(host_pause_min_seconds=30, host_pause_max_seconds=90, interval_jitter=0.1, failure_backoff_seconds=900)


class FakeClock:
    """A clock that only moves when the test advances it."""

    def __init__(self) -> None:
        """Starts the clock at START."""
        self.now = START

    def __call__(self) -> datetime:
        """Returns the current fake time."""
        return self.now


def _kind(planned_requests: list[PlannedRequest], run: Callable[[MetadataTask], None], name: str = "synthetic") -> TaskKind:
    """Builds a task kind with a fixed plan and the given handler."""
    return TaskKind(name, INTERVAL, lambda: planned_requests, run)


def _worker(db: Database, kinds: list[TaskKind], clock: FakeClock) -> MetadataWorker:
    """Builds a worker on the test database with the fake clock and a seeded random source."""
    return MetadataWorker(MongoMetadataTaskRepository(db), kinds, SETTINGS, clock=clock, rng=random.Random(0))


def _stored(db: Database, key: str) -> MetadataTask:
    """Reads one stored task."""
    task = MongoMetadataTaskRepository(db).get(key)
    assert task is not None
    return task


@pytest.mark.integration
def test_sync_spreads_first_runs_keeps_state_and_deactivates_unplanned_tasks(db: Database) -> None:
    """New per-dataset tasks start within one interval, a re-sync keeps their state, and unplanned tasks stay stored but inactive."""
    repository = MongoMetadataTaskRepository(db)
    planned_requests = [PlannedRequest(f"dataset_{index}", "https://a.example.org/x") for index in range(20)]
    repository.sync("synthetic", planned_requests, INTERVAL, START)
    first_due_times = [_stored(db, f"synthetic/dataset_{index}").next_due_at for index in range(20)]
    repository.record_success("synthetic/dataset_0", START, START + INTERVAL)

    repository.sync("synthetic", planned_requests[:1], INTERVAL, START + timedelta(hours=1))

    assert all(START <= due_at < START + INTERVAL for due_at in first_due_times)
    assert len(set(first_due_times)) > 1
    kept, dropped = _stored(db, "synthetic/dataset_0"), _stored(db, "synthetic/dataset_1")
    assert (kept.active, kept.last_success_at, kept.next_due_at) == (True, START, START + INTERVAL)
    assert (dropped.active, dropped.next_due_at) == (False, first_due_times[1])


@pytest.mark.integration
def test_single_request_kind_runs_at_once_after_planning(db: Database) -> None:
    """A kind with one single request runs in the same round it is planned, while a per-dataset task waits for its spread start."""
    clock = FakeClock()
    runs: list[str] = []
    planned_requests = [PlannedRequest("all", "https://a.example.org/x")]
    per_dataset = [PlannedRequest("dataset_a", "https://b.example.org/x")]
    worker = _worker(
        db, [_kind(planned_requests, lambda task: runs.append(task.id)), _kind(per_dataset, lambda task: runs.append(task.id), "spread")], clock
    )

    ran = worker.run_once()

    assert (ran, runs, _stored(db, "spread/dataset_a").next_due_at > START) == (True, ["synthetic/all"], True)


@pytest.mark.integration
def test_due_task_runs_once_and_is_scheduled_one_jittered_interval_later(db: Database) -> None:
    """A task runs only when due, and its next run lies one interval plus or minus the jitter after the run."""
    clock = FakeClock()
    runs: list[str] = []
    worker = _worker(db, [_kind([PlannedRequest("dataset_a", "https://a.example.org/x")], lambda task: runs.append(task.id))], clock)
    worker.run_once()
    due_at = _stored(db, "synthetic/dataset_a").next_due_at
    clock.now = due_at - timedelta(seconds=1)
    ran_before_due = worker.run_once()

    clock.now = due_at
    ran_when_due = worker.run_once()

    task = _stored(db, "synthetic/dataset_a")
    assert (ran_before_due, ran_when_due, runs, task.last_success_at) == (False, True, ["synthetic/dataset_a"], due_at)
    assert due_at + INTERVAL * 0.9 <= task.next_due_at <= due_at + INTERVAL * 1.1


@pytest.mark.integration
def test_failures_back_off_doubling_and_a_success_resets_them(db: Database) -> None:
    """Each failure doubles the retry delay starting at the backoff setting, and the next success clears the failure state."""
    clock = FakeClock()
    outcomes = [DataRetrievalError("first"), DataRetrievalError("second"), None]

    def run(task: MetadataTask) -> None:
        """Raises the next planned failure or succeeds."""
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome

    worker = _worker(db, [_kind([PlannedRequest("dataset_a", "https://a.example.org/x")], run)], clock)
    worker.run_once()
    clock.now = START + INTERVAL
    worker.run_once()
    first_failure = _stored(db, "synthetic/dataset_a")
    first_delay = first_failure.next_due_at - clock.now
    clock.now = first_failure.next_due_at + timedelta(seconds=SETTINGS.host_pause_max_seconds)
    worker.run_once()
    second_failure = _stored(db, "synthetic/dataset_a")
    second_delay = second_failure.next_due_at - clock.now
    clock.now = second_failure.next_due_at + timedelta(seconds=SETTINGS.host_pause_max_seconds)
    worker.run_once()

    assert [(first_delay, first_failure.consecutive_failures, first_failure.last_error), (second_delay, second_failure.consecutive_failures)] == [
        (timedelta(seconds=900), 1, "first"),
        (timedelta(seconds=1800), 2),
    ]
    task = _stored(db, "synthetic/dataset_a")
    assert (task.consecutive_failures, task.last_error, task.last_success_at) == (0, None, clock.now)


@pytest.mark.integration
def test_long_failure_run_retries_after_one_interval_without_overflow(db: Database) -> None:
    """After 40 failures in a row the doubled backoff is capped at the interval instead of overflowing the time arithmetic."""
    clock = FakeClock()

    def run(task: MetadataTask) -> None:
        """Fails every run."""
        raise DataRetrievalError("down")

    worker = _worker(db, [_kind([PlannedRequest("dataset_a", "https://a.example.org/x")], run)], clock)
    worker.run_once()
    db["metadata_tasks"].update_one({"_id": "synthetic/dataset_a"}, {"$set": {"consecutive_failures": 40}})
    clock.now = START + INTERVAL

    worker.run_once()

    task = _stored(db, "synthetic/dataset_a")
    assert (task.consecutive_failures, task.next_due_at) == (41, clock.now + INTERVAL)


@pytest.mark.integration
def test_empty_plan_keeps_the_stored_tasks_active(db: Database) -> None:
    """A kind that plans nothing, as before its source data is loaded, leaves its stored tasks active."""
    clock = FakeClock()
    planned_requests = [PlannedRequest("dataset_a", "https://a.example.org/x")]
    _worker(db, [_kind(planned_requests, lambda task: None)], clock).run_once()
    planned_requests.clear()

    _worker(db, [_kind(planned_requests, lambda task: None)], clock).run_once()

    assert _stored(db, "synthetic/dataset_a").active is True


@pytest.mark.integration
def test_requests_to_one_host_pause_while_other_hosts_run(db: Database) -> None:
    """After a request, its host waits a random pause within the settings, while a due task of another host runs at once."""
    clock = FakeClock()
    runs: list[str] = []
    planned_requests = [
        PlannedRequest("a1", "https://a.example.org/1"),
        PlannedRequest("a2", "https://A.example.org/2"),
        PlannedRequest("b1", "https://b.example.org/1"),
    ]
    worker = _worker(db, [_kind(planned_requests, lambda task: runs.append(task.target))], clock)
    worker.run_once()
    clock.now = START + INTERVAL

    first_round = [worker.run_once() for _ in range(3)]
    clock.now += timedelta(seconds=SETTINGS.host_pause_min_seconds - 1)
    during_pause = worker.run_once()
    clock.now += timedelta(seconds=SETTINGS.host_pause_max_seconds)
    after_pause = worker.run_once()

    assert (first_round, during_pause, after_pause) == ([True, True, False], False, True)
    # Host names compare case-insensitively, so a1 and a2 share one pause
    assert ({runs[0][0], runs[1][0]}, sorted(runs)) == ({"a", "b"}, ["a1", "a2", "b1"])


@pytest.mark.integration
def test_hapi_mirror_tasks_record_failure_when_the_mirror_is_unreachable(db: Database, closed_port_url: str) -> None:
    """Both HAPI mirror tasks are planned and run at once, and an unreachable mirror counts as failed run instead of success."""
    clock = FakeClock()
    mirror_base_url = f"{closed_port_url}/meta/"
    kinds = [hapi_metadata_kind(db, TaskIntervalSettings(), mirror_base_url), hapi_availability_kind(db, TaskIntervalSettings(), [], mirror_base_url)]
    worker = _worker(db, kinds, clock)

    worker.run_once()
    clock.now += timedelta(seconds=SETTINGS.host_pause_max_seconds)
    worker.run_once()

    tasks = [_stored(db, "hapi_mirror_metadata/all"), _stored(db, "hapi_mirror_availability/all")]
    assert [(task.consecutive_failures, task.last_success_at, task.last_error is not None) for task in tasks] == [(1, None, True), (1, None, True)]


@pytest.mark.integration
def test_retry_after_lengthens_the_retry_delay_and_the_host_pause(db: Database) -> None:
    """A failure naming a Retry-After longer than the backoff delays the retry and every request to the same host by that wait."""
    clock = FakeClock()
    runs: list[str] = []

    def run(task: MetadataTask) -> None:
        """Records the run and fails the busy task with a Retry-After of two hours."""
        runs.append(task.target)
        if task.target == "busy":
            raise DataRetrievalError("busy", retry_after_seconds=7200)

    planned_requests = [PlannedRequest("busy", "https://a.example.org/1"), PlannedRequest("other", "https://a.example.org/2")]
    worker = _worker(db, [_kind(planned_requests, run)], clock)
    worker.run_once()
    repository = MongoMetadataTaskRepository(db)
    repository.record_success("synthetic/busy", START, START + timedelta(hours=1))
    repository.record_success("synthetic/other", START, START + timedelta(hours=1, seconds=1))
    clock.now = START + timedelta(hours=1, seconds=1)
    worker.run_once()
    failed_at = clock.now

    clock.now = failed_at + timedelta(seconds=SETTINGS.host_pause_max_seconds)
    during_retry_after = worker.run_once()
    clock.now = failed_at + timedelta(seconds=7200)
    after_retry_after = worker.run_once()

    assert (runs, during_retry_after, after_retry_after) == (["busy", "other"], False, True)
    assert _stored(db, "synthetic/busy").next_due_at == failed_at + timedelta(seconds=7200)
