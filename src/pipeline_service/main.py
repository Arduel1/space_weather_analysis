import logging
import pathlib
import sys
import tomllib
from datetime import UTC, datetime

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from pipeline_service.feeds.models import load_feeds
from pipeline_service.feeds.scheduler import queue_feed_jobs
from pipeline_service.jobs.runner import JobRunner
from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.database.connection import connect_from_environment
from space_weather_analysis.jobs.repository import MongoJobRepository
from space_weather_analysis.logging.config import setup_logging

logger = logging.getLogger(__name__)


class PipelineSettings(BaseSettings):
    """Settings of the pipeline service, read from environment variables `PIPELINE_{field name}` in upper case.

    Attributes:
        data_dir: The directory holding the local dataset files.
        feeds_file: The TOML file listing the feeds whose jobs are queued on every start.
    """

    model_config = SettingsConfigDict(env_prefix="PIPELINE_", frozen=True)

    data_dir: pathlib.Path = pathlib.Path("data")
    feeds_file: pathlib.Path = pathlib.Path("config/feeds.toml")


def main() -> None:
    """Queues the jobs of the configured feeds and runs the job daemon until the process ends."""
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
        queue_feed_jobs(job_repository, feeds, datetime.now(UTC))
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
