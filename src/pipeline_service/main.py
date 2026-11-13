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
from space_weather_analysis.catalog.models import MasterCatalogItem
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.catalog.static_entries import load_static_catalog
from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.database.connection import connect_from_environment
from space_weather_analysis.jobs.repository import JobRepository, MongoJobRepository
from space_weather_analysis.logging.config import setup_logging

logger = logging.getLogger(__name__)


class PipelineSettings(BaseSettings):
    """Settings of the pipeline service, read from environment variables `PIPELINE_{field name}` in upper case.

    Attributes:
        catalog_file: The TOML file listing the master catalog items saved on start for the datasets the catalog does not hold yet.
        data_dir: The directory holding the local dataset files.
        feeds_file: The TOML file listing the feeds whose jobs are queued on start and then periodically.
        run_once: Whether every feed is loaded once and the service then ends, instead of running until the process ends.
    """

    model_config = SettingsConfigDict(env_prefix="PIPELINE_", frozen=True)

    catalog_file: pathlib.Path = pathlib.Path("config/sources/catalog.toml")
    data_dir: pathlib.Path = pathlib.Path("data")
    feeds_file: pathlib.Path = pathlib.Path("config/feeds.toml")
    run_once: bool = False


def save_static_catalog(catalog_repository: CatalogRepository, items: list[MasterCatalogItem]) -> None:
    """Saves the configured catalog items of the datasets the catalog does not hold yet.

    Args:
        catalog_repository: The master catalog.
        items: The configured catalog items.

    Raises:
        pymongo.errors.PyMongoError: If the catalog cannot be written.
    """
    inserted = catalog_repository.save_missing(items)
    if inserted:
        logger.info("Saved the configured catalog items of %d datasets the catalog did not hold: %s", len(inserted), inserted)


def run_once(db: Database, job_repository: JobRepository, feeds: list[Feed], data_dir: pathlib.Path, now: datetime) -> None:
    """Queues one job per feed and runs the jobs until none is due.

    Nothing fills the master catalog during the run, so a feed without source type is left out unless the catalog
    holds every one of its datasets, and a job naming a dataset the catalog does not hold fails at once.

    Args:
        db: The database holding jobs, catalog and data.
        job_repository: The job queue.
        feeds: The configured feeds.
        data_dir: The directory holding the local dataset files.
        now: The current time, the end of lookback ranges.

    Raises:
        pymongo.errors.PyMongoError: If the queue or the catalog cannot be read or written.
    """
    catalog_repository = MongoCatalogRepository(db)
    runnable_feeds = []
    for feed in feeds:
        missing_datasets = [] if feed.source_type is not None else _uncatalogued(catalog_repository, feed.datasets)
        if missing_datasets:
            logger.warning("Feed %s is left out, the master catalog does not hold its datasets %s", feed.name, missing_datasets)
        else:
            runnable_feeds.append(feed)
    queue_feed_jobs(job_repository, runnable_feeds, now)
    JobRunner(db, data_dir, catalog_wait_limit=timedelta(0)).run_until_idle()


def _uncatalogued(catalog_repository: CatalogRepository, dataset_ids: list[str]) -> list[str]:
    """Lists the datasets without any source in the master catalog.

    Args:
        catalog_repository: The master catalog.
        dataset_ids: The dataset IDs to check.

    Returns:
        The IDs of the datasets without source, in the given order.
    """
    return [dataset_id for dataset_id in dataset_ids if not catalog_repository.get_sources(dataset_id)]


def main() -> None:
    """Saves the missing static catalog items, then queues the feed jobs and runs them in their intervals, or once with PIPELINE_RUN_ONCE."""
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
    try:
        static_catalog = load_static_catalog(settings.catalog_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        logger.critical("Cannot read the static catalog file %s: %s. Exiting.", settings.catalog_file, error)
        sys.exit(1)
    except ValidationError as error:
        logger.critical("Invalid static catalog file %s: %s. Exiting.", settings.catalog_file, format_settings_error(error))
        sys.exit(1)

    connection, db = connect_from_environment()
    try:
        save_static_catalog(MongoCatalogRepository(db), static_catalog)
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
