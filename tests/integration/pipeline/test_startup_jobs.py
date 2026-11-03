from datetime import UTC, datetime, timedelta

import pytest
from pymongo.database import Database

from pipeline_service.feeds.models import Feed
from pipeline_service.feeds.scheduler import queue_feed_jobs
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.jobs.models import JobStatus
from space_weather_analysis.jobs.repository import MongoJobRepository

NOW = datetime(2024, 3, 1, tzinfo=UTC)
FEEDS = [
    Feed(name="kp", datasets=["Kp"], lookback=timedelta(days=2)),
    Feed(name="local", datasets=["silso_sunspot_daily"], source_type=SourceType.LOCAL),
]


@pytest.mark.integration
def test_feed_jobs_are_queued_with_their_range_and_once_while_open(db: Database) -> None:
    """Each feed gets a pending job with its resolved range; a second start queues nothing for feeds whose job is still open."""
    repository = MongoJobRepository(db)

    queue_feed_jobs(repository, FEEDS, NOW)
    queue_feed_jobs(repository, FEEDS, NOW + timedelta(hours=1))

    jobs = [repository.claim_next_pending(), repository.claim_next_pending(), repository.claim_next_pending()]
    assert jobs[2] is None
    assert [(job.feed, job.source_type, job.parameters.start_time, job.parameters.end_time) for job in jobs[:2] if job] == [
        ("kp", None, NOW - timedelta(days=2), NOW),
        ("local", SourceType.LOCAL, None, None),
    ]


@pytest.mark.integration
def test_feed_whose_job_ended_gets_a_new_job(db: Database) -> None:
    """Once the job of a feed has ended, the next start queues a new one."""
    repository = MongoJobRepository(db)
    queue_feed_jobs(repository, FEEDS[:1], NOW)
    first_job = repository.claim_next_pending()
    assert first_job is not None
    repository.finish(first_job.id, JobStatus.COMPLETED)

    queue_feed_jobs(repository, FEEDS[:1], NOW + timedelta(days=1))

    second_job = repository.claim_next_pending()
    assert second_job is not None and second_job.parameters.end_time == NOW + timedelta(days=1)
