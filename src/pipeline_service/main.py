import logging
import pathlib
import sys
import threading
import tomllib
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from pymongo.database import Database

from pipeline_service.feeds.models import Feed, load_feeds
from pipeline_service.feeds.scheduler import queue_feed_jobs, run_feed_scheduler
from pipeline_service.jobs.runner import JobRunner
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.database.connection import connect_from_environment
from space_weather_analysis.jobs.repository import JobRepository, MongoJobRepository
from space_weather_analysis.logging.config import setup_logging

logger = logging.getLogger(__name__)


class PipelineSettings(BaseSettings):
    """Settings of the pipeline service, read from environment variables `PIPELINE_{field name}` in upper case.

    Attributes:
        data_dir: The directory holding the local dataset files.
        feeds_file: The TOML file listing the feeds whose jobs are queued on start and then periodically.
        run_once: Whether every feed is loaded once and the service then ends, instead of running until the process ends.
    """

    model_config = SettingsConfigDict(env_prefix="PIPELINE_", frozen=True)

    data_dir: pathlib.Path = pathlib.Path("data")
    feeds_file: pathlib.Path = pathlib.Path("config/feeds.toml")
    run_once: bool = False


def run_once(db: Database, job_repository: JobRepository, feeds: list[Feed], data_dir: pathlib.Path, now: datetime) -> None:
    """Queues one job per feed and runs the jobs until none is due.

    Nothing fills the master catalog during the run, so with an empty catalog the feeds without source type are left
    out, and a job naming a dataset the catalog does not hold fails at once.

    Args:
        db: The database holding jobs, catalog and data.
        job_repository: The job queue.
        feeds: The configured feeds.
        data_dir: The directory holding the local dataset files.
        now: The current time, the end of lookback ranges.

    Raises:
        pymongo.errors.PyMongoError: If the queue or the catalog cannot be read or written.
    """
    if MongoCatalogRepository(db).is_empty():
        skipped = [feed.name for feed in feeds if feed.source_type is None]
        if skipped:
            logger.warning("The master catalog is empty, so the feeds without source type are left out: %s", skipped)
        feeds = [feed for feed in feeds if feed.source_type is not None]
    queue_feed_jobs(job_repository, feeds, now)
    JobRunner(db, data_dir, catalog_wait_limit=timedelta(0)).run_until_idle()


def main() -> None:
    """Queues the jobs of the configured feeds and runs them, repeated in their intervals until the process ends or once with PIPELINE_RUN_ONCE."""
    setup_logging(service_name="PipelineService")

    try:
        settings = PipelineSettings()
    except ValidationError as error:
        logger.critical("Invalid pipeline settings, check the PIPELINE_* environment variables: %s. Exiting.", format_settings_error(error))
        sys.exit(1)
    try:
        feeds = load_feeds(settings.feeds_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        logger.critical("Cannot read the feed file %s: %s. Exiting.", settings.feeds_file, error)
        sys.exit(1)
    except ValidationError as error:
        logger.critical("Invalid feed file %s: %s. Exiting.", settings.feeds_file, format_settings_error(error))
        sys.exit(1)

    connection, db = connect_from_environment()
    try:
        job_repository = MongoJobRepository(db)
        # One job runner serves the queue, so a running job found at start was interrupted by the previous run
        requeued = job_repository.requeue_running()
        if requeued:
            logger.info("Requeued %d jobs interrupted by the previous run", requeued)
        started_at = datetime.now(UTC)
        if settings.run_once:
            run_once(db, job_repository, feeds, settings.data_dir, started_at)
            logger.info("Every feed was loaded once. Exiting.")
            return
        queue_feed_jobs(job_repository, feeds, started_at)
        if any(feed.interval is not None for feed in feeds):
            # Daemon thread, as the scheduler never returns and ends with the process
            threading.Thread(target=run_feed_scheduler, args=(job_repository, feeds, started_at), name="feed-scheduler", daemon=True).start()
        JobRunner(db, settings.data_dir).run_daemon()
    except KeyboardInterrupt:
        logger.info("Pipeline service stopped.")
    except Exception:
        logger.exception("Pipeline service failed. Exiting.")
        sys.exit(1)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
