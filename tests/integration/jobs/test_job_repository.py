from datetime import UTC, datetime, timedelta

import pytest
from pymongo.database import Database

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.jobs.models import ChunkGap, FeedJobSummary, FinalJobStatus, Job, JobParameters, JobStatus
from space_weather_analysis.jobs.repository import MongoJobRepository

START = datetime(2024, 1, 1, tzinfo=UTC)
END = datetime(2024, 1, 8, tzinfo=UTC)


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
    assert job.created_at.tzinfo == UTC


@pytest.mark.integration
def test_claim_next_pending_takes_each_pending_job_once(db: Database) -> None:
    """Claiming marks the oldest pending job as RUNNING with a start time and never hands out a job twice."""
    repository = MongoJobRepository(db)
    first_id = repository.create(["synthetic/one"], START, END)
    second_id = repository.create(["synthetic/two"], None, None, source_type=SourceType.HAPI)

    claimed = [repository.claim_next_pending(), repository.claim_next_pending(), repository.claim_next_pending()]

    assert [(job.id, job.status, job.source_type) if job else None for job in claimed] == [
        (first_id, "RUNNING", None),
        (second_id, "RUNNING", "HAPI"),
        None,
    ]
    assert all(job is not None and job.started_at is not None for job in claimed[:2])


@pytest.mark.integration
def test_claim_next_pending_takes_only_jobs_of_the_given_source_types(db: Database) -> None:
    """A claim limited to source types skips older jobs of other types; None stands for jobs without source type."""
    repository = MongoJobRepository(db)
    untyped_id = repository.create(["synthetic/one"], START, END)
    hapi_id = repository.create(["synthetic/two"], START, END, source_type=SourceType.HAPI)

    claimed_hapi = repository.claim_next_pending(source_types=[SourceType.HAPI])
    claimed_gfz = repository.claim_next_pending(source_types=[SourceType.GFZ_POTSDAM])
    claimed_untyped = repository.claim_next_pending(source_types=[None])

    assert (claimed_hapi and claimed_hapi.id, claimed_gfz, claimed_untyped and claimed_untyped.id) == (hapi_id, None, untyped_id)


@pytest.mark.integration
def test_claim_next_pending_fails_an_unreadable_job_and_takes_the_next(db: Database) -> None:
    """A pending job document without creation time ends FAILED naming the field, and the claim returns the next due job."""
    repository = MongoJobRepository(db)
    db[MongoJobRepository.COLLECTION].insert_one({"_id": "written-by-hand", "status": "PENDING", "parameters": {"datasets": ["synthetic/one"]}})
    valid_id = repository.create(["synthetic/two"], START, END)

    claimed = repository.claim_next_pending()

    unreadable = db[MongoJobRepository.COLLECTION].find_one({"_id": "written-by-hand"})
    assert claimed is not None and claimed.id == valid_id
    assert unreadable is not None
    assert (unreadable["status"], unreadable["error"]) == ("FAILED", "The job document does not match the job schema in: created_at")
    assert unreadable["completed_at"] is not None


@pytest.mark.integration
def test_fail_stale_running_fails_only_jobs_silent_beyond_the_limit(db: Database) -> None:
    """A running job started before the limit ends FAILED; one started within it or one reporting progress keeps running."""
    repository = MongoJobRepository(db)
    now = datetime.now(UTC)
    limit = timedelta(minutes=30)
    silent_id, recent_id, progressing_id = (repository.create([f"synthetic/{name}"], START, END) for name in ("silent", "recent", "progressing"))
    for started_ago in (timedelta(minutes=31), timedelta(minutes=29), timedelta(minutes=31)):
        repository.claim_next_pending(now - started_ago)
    repository.update_progress(progressing_id, 0.5, "chunk 1/2 done")

    failed = repository.fail_stale_running(now, limit)

    statuses = [job.status if job else None for job in (repository.get(silent_id), repository.get(recent_id), repository.get(progressing_id))]
    stored_silent = repository.get(silent_id)
    assert [job.id for job in failed] == [silent_id]
    assert statuses == [JobStatus.FAILED, JobStatus.RUNNING, JobStatus.RUNNING]
    assert stored_silent is not None and stored_silent.error == "No progress for 0:30:00"


@pytest.mark.integration
def test_feed_summaries_name_the_newest_job_and_count_outcomes_in_the_span(db: Database) -> None:
    """Per feed the newest job is named and the outcomes of jobs created since the start are counted; jobs without feed are left out."""
    repository = MongoJobRepository(db)
    gap = ChunkGap(dataset_id="synthetic/one", start=START, stop=END, reason="SourceUnreachableError: no answer")
    outcomes: list[tuple[FinalJobStatus, tuple[ChunkGap, ...]]] = [(JobStatus.COMPLETED, ()), (JobStatus.COMPLETED, (gap,)), (JobStatus.FAILED, ())]
    for status, missing_chunks in outcomes:
        job_id = repository.create(["synthetic/one"], START, END, feed="live")
        repository.finish(job_id, status, missing_chunks=missing_chunks)
    repository.create(["synthetic/two"], START, END, feed="start-only")
    repository.create(["synthetic/three"], START, END)

    in_span = repository.feed_summaries(datetime(2000, 1, 1, tzinfo=UTC))
    after_span = repository.feed_summaries(datetime(2999, 1, 1, tzinfo=UTC))

    def outline(summaries: list[FeedJobSummary]) -> list[tuple[str, str, int, int, int, int]]:
        return [(summary.feed, summary.latest.status, summary.runs, summary.completed, summary.with_gaps, summary.failed) for summary in summaries]

    assert outline(in_span) == [("live", "FAILED", 3, 1, 1, 1), ("start-only", "PENDING", 1, 0, 0, 0)]
    assert outline(after_span) == [("live", "FAILED", 0, 0, 0, 0), ("start-only", "PENDING", 0, 0, 0, 0)]


@pytest.mark.integration
def test_progress_and_failure_are_recorded(db: Database) -> None:
    """Progress updates and a FAILED finish are stored with their times and the error description."""
    repository = MongoJobRepository(db)
    job_id = repository.create(["synthetic/one"], START, END)
    repository.claim_next_pending()

    repository.update_progress(job_id, 0.5, "Chunk 1/2")
    repository.finish(job_id, JobStatus.FAILED, "Server unreachable")

    job = repository.get(job_id)
    assert job is not None
    assert (job.status, job.progress, job.message, job.error) == ("FAILED", 0.5, "Chunk 1/2", "Server unreachable")
    assert job.updated_at is not None and job.completed_at is not None


@pytest.mark.integration
def test_completed_job_keeps_its_missing_chunks(db: Database) -> None:
    """A completed job reads back with the progress 1.0 and the time ranges it could not ingest."""
    repository = MongoJobRepository(db)
    job_id = repository.create(["synthetic/one"], START, END)
    gap = ChunkGap(dataset_id="synthetic/one", start=START, stop=END, reason="DataRetrievalError: Server unreachable")

    repository.finish(job_id, JobStatus.COMPLETED, missing_chunks=[gap])

    job = repository.get(job_id)
    assert job is not None
    assert (job.status, job.progress, job.error, job.missing_chunks) == (JobStatus.COMPLETED, 1.0, None, [gap])


@pytest.mark.parametrize("job_id", ["not-an-object-id", "0123456789abcdef01234567"], ids=["malformed", "unknown"])
@pytest.mark.integration
def test_get_returns_none_for_missing_job(db: Database, job_id: str) -> None:
    """A malformed or unknown job ID reads as no job."""
    assert MongoJobRepository(db).get(job_id) is None


@pytest.mark.integration
def test_running_jobs_are_requeued_and_a_requeued_job_waits_for_its_retry_time(db: Database) -> None:
    """Requeuing running jobs sets them back to PENDING, and a job requeued with a retry time is claimed only from that time on."""
    repository = MongoJobRepository(db)
    repository.create(["synthetic/one"], START, END)
    interrupted = repository.claim_next_pending(START)
    assert interrupted is not None

    requeued_count = repository.requeue_running()
    reclaimed = repository.claim_next_pending(START)
    assert reclaimed is not None
    repository.requeue(reclaimed.id, END, "waits")

    assert (requeued_count, reclaimed.id, repository.claim_next_pending(START)) == (1, interrupted.id, None)
    claimed_at_retry = repository.claim_next_pending(END)
    assert claimed_at_retry is not None and claimed_at_retry.id == interrupted.id
