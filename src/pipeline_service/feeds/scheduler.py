import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from time import sleep

from pymongo.errors import PyMongoError

from pipeline_service.feeds.models import Feed
from space_weather_analysis.jobs.repository import JobRepository

logger = logging.getLogger(__name__)

# Time between two checks for feeds whose interval has passed
CHECK_INTERVAL = timedelta(minutes=1)


def queue_feed_jobs(job_repository: JobRepository, feeds: Sequence[Feed], now: datetime) -> None:
    """Queues one job per feed, unless a job of the feed is still pending or running.

    Args:
        job_repository: The job queue.
        feeds: The feeds to queue a job for.
        now: The current time, the end of lookback ranges.
    """
    for feed in feeds:
        if job_repository.has_open_job(feed.name):
            logger.info("Feed %s still has an open job, no further job queued", feed.name)
            continue
        start, end = feed.time_range(now)
        job_id = job_repository.create(feed.datasets, start, end, feed.source_type, feed.name)
        logger.info("Queued job %s of feed %s for %s from %s to %s", job_id, feed.name, feed.datasets, start, end)


def due_feeds(feeds: Sequence[Feed], last_queued: Mapping[str, datetime], now: datetime) -> list[Feed]:
    """Selects the feeds whose interval has passed since their last queuing.

    Args:
        feeds: The configured feeds.
        last_queued: When each feed was last queued, by feed name; a feed missing here counts as due.
        now: The current time.

    Returns:
        The due feeds in the order of the configuration; feeds without interval are never due.
    """
    due = []
    for feed in feeds:
        if feed.interval is None:
            continue
        queued_at = last_queued.get(feed.name)
        if queued_at is None or now - queued_at >= feed.interval:
            due.append(feed)
    return due


def run_feed_scheduler(
    job_repository: JobRepository, feeds: Sequence[Feed], started_at: datetime, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> None:
    """Queues the jobs of the feeds with an interval whenever it has passed, checking every CHECK_INTERVAL until the process ends.

    Args:
        job_repository: The job queue.
        feeds: The configured feeds.
        started_at: When the jobs of all feeds were queued on start.
        clock: Returns the current time in UTC.
    """
    last_queued = dict.fromkeys((feed.name for feed in feeds), started_at)
    while True:
        sleep(CHECK_INTERVAL.total_seconds())
        now = clock()
        due = due_feeds(feeds, last_queued, now)
        try:
            queue_feed_jobs(job_repository, due, now)
        except PyMongoError:
            logger.exception("Queuing the jobs of the due feeds failed")
            continue
        for feed in due:
            last_queued[feed.name] = now
