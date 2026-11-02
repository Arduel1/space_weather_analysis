from datetime import UTC, datetime

import pytest
from pymongo.database import Database

from pipeline_service.main import PipelineSettings, enqueue_startup_jobs
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.jobs.models import JobStatus
from space_weather_analysis.jobs.repository import MongoJobRepository

STARTUP_JOBS_JSON = (
    '[{"datasets": ["Kp"], "start_time": "2024-01-01T00:00:00Z", "end_time": "2024-01-02T00:00:00Z", "source_type": "GFZ_POTSDAM"},'
    ' {"datasets": ["silso_sunspot_daily"]}]'
)


@pytest.mark.integration
def test_startup_jobs_from_the_environment_are_queued_as_pending_jobs(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each startup job of PIPELINE_STARTUP_JOBS becomes a pending job with its parameters, in the given order."""
    monkeypatch.setenv("PIPELINE_STARTUP_JOBS", STARTUP_JOBS_JSON)
    repository = MongoJobRepository(db)

    enqueue_startup_jobs(repository, PipelineSettings().startup_jobs)

    first, second, rest = repository.claim_next_pending(), repository.claim_next_pending(), repository.claim_next_pending()
    assert first is not None and second is not None and rest is None
    assert (first.status, first.source_type, first.parameters.datasets, first.parameters.start_time, first.parameters.end_time) == (
        JobStatus.RUNNING,
        SourceType.GFZ_POTSDAM,
        ["Kp"],
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
    )
    assert (second.source_type, second.parameters.datasets, second.parameters.start_time) == (None, ["silso_sunspot_daily"], None)


def test_startup_jobs_are_empty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without PIPELINE_STARTUP_JOBS the service queues no job."""
    monkeypatch.delenv("PIPELINE_STARTUP_JOBS", raising=False)

    assert PipelineSettings().startup_jobs == []
