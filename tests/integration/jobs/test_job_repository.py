from datetime import datetime, timezone

import pytest
from pymongo.database import Database

from space_weather_analysis.jobs.models import Job, JobParameters
from space_weather_analysis.jobs.repository import MongoJobRepository

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 8, tzinfo=timezone.utc)


@pytest.mark.integration
def test_created_job_reads_back_as_pending(db: Database) -> None:
    """A created job reads back as PENDING with its parameters, the initial message and the times in UTC."""
    repository = MongoJobRepository(db)

    job_id = repository.create(["synthetic/one"], START, END)

    job = repository.get(job_id)
    assert job is not None
    assert job == Job(
        id=job_id,
        status="PENDING",
        parameters=JobParameters(datasets=["synthetic/one"], start_time=START, end_time=END),
        created_at=job.created_at,
        progress=0.0,
        message="Job created",
    )
    assert job.created_at.tzinfo == timezone.utc


@pytest.mark.integration
def test_claim_next_pending_takes_each_pending_job_once(db: Database) -> None:
    """Claiming marks the oldest pending job as RUNNING with a start time and never hands out a job twice."""
    repository = MongoJobRepository(db)
    first_id = repository.create(["synthetic/one"], START, END)
    second_id = repository.create(["synthetic/two"], None, None, source_id="HAPI")

    claimed = [repository.claim_next_pending(), repository.claim_next_pending(), repository.claim_next_pending()]

    assert [(job.id, job.status, job.source_id) if job else None for job in claimed] == [
        (first_id, "RUNNING", None),
        (second_id, "RUNNING", "HAPI"),
        None,
    ]
    assert all(job is not None and job.started_at is not None for job in claimed[:2])


@pytest.mark.integration
def test_progress_and_failure_are_recorded(db: Database) -> None:
    """Progress updates and a FAILED finish are stored with their times and the error description."""
    repository = MongoJobRepository(db)
    job_id = repository.create(["synthetic/one"], START, END)
    repository.claim_next_pending()

    repository.update_progress(job_id, 0.5, "Chunk 1/2")
    repository.finish(job_id, "FAILED", "Server unreachable")

    job = repository.get(job_id)
    assert job is not None
    assert (job.status, job.progress, job.message, job.error) == ("FAILED", 0.5, "Chunk 1/2", "Server unreachable")
    assert job.updated_at is not None and job.completed_at is not None


@pytest.mark.parametrize("job_id", ["not-an-object-id", "0123456789abcdef01234567"], ids=["malformed", "unknown"])
@pytest.mark.integration
def test_get_returns_none_for_missing_job(db: Database, job_id: str) -> None:
    """A malformed or unknown job ID reads as no job."""
    assert MongoJobRepository(db).get(job_id) is None
