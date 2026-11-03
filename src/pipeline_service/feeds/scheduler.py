import logging
from collections.abc import Sequence
from datetime import datetime

from pipeline_service.feeds.models import Feed
from space_weather_analysis.jobs.repository import JobRepository

logger = logging.getLogger(__name__)


def queue_feed_jobs(job_repository: JobRepository, feeds: Sequence[Feed], now: datetime) -> None:
    """Queues one job per feed, unless a job of the feed is still pending or running.

    Args:
        job_repository: The job queue.
        feeds: The configured feeds.
        now: The current time, the end of lookback ranges.
    """
    for feed in feeds:
        if job_repository.has_open_job(feed.name):
            logger.info("Feed %s still has an open job, no further job queued", feed.name)
            continue
        start, end = feed.time_range(now)
        job_id = job_repository.create(feed.datasets, start, end, feed.source_type, feed.name)
        logger.info("Queued job %s of feed %s for %s from %s to %s", job_id, feed.name, feed.datasets, start, end)
