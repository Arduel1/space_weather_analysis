import logging
import pathlib
import sys

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from pipeline_service.orchestrator import PipelineOrchestrator
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.database.connection import connect_from_environment
from space_weather_analysis.jobs.models import JobParameters
from space_weather_analysis.jobs.repository import JobRepository, MongoJobRepository
from space_weather_analysis.logging.config import setup_logging

logger = logging.getLogger(__name__)


class StartupJob(JobParameters):
    """A download job queued when the service starts.

    Attributes:
        source_type: The source type of the pipeline to run; None lets the orchestrator resolve it from the master catalog.
    """

    source_type: SourceType | None = None


class PipelineSettings(BaseSettings):
    """Settings of the pipeline service, read from environment variables `PIPELINE_{field name}` in upper case.

    Attributes:
        data_dir: The directory holding the local dataset files.
        startup_jobs: The jobs queued on every start, given as JSON list of objects with datasets, start_time, end_time and source_type.
    """

    model_config = SettingsConfigDict(env_prefix="PIPELINE_", frozen=True)

    data_dir: pathlib.Path = pathlib.Path("data")
    startup_jobs: list[StartupJob] = []


def enqueue_startup_jobs(job_repository: JobRepository, startup_jobs: list[StartupJob]) -> None:
    """Queues the startup jobs, so the job daemon runs them like the jobs the frontend requests.

    Args:
        job_repository: The job queue.
        startup_jobs: The jobs to queue.
    """
    for startup_job in startup_jobs:
        job_id = job_repository.create(startup_job.datasets, startup_job.start_time, startup_job.end_time, startup_job.source_type)
        logger.info("Queued startup job %s for %s", job_id, startup_job.datasets)


def main() -> None:
    """Queues the configured startup jobs and runs the job daemon until the process ends."""
    setup_logging(service_name="PipelineService")

    try:
        settings = PipelineSettings()
    except ValidationError as error:
        logger.critical("Invalid pipeline settings, check the PIPELINE_* environment variables: %s. Exiting.", format_settings_error(error))
        sys.exit(1)

    connection, db = connect_from_environment()
    try:
        enqueue_startup_jobs(MongoJobRepository(db), settings.startup_jobs)
        PipelineOrchestrator(db, settings.data_dir).run_daemon()
    except KeyboardInterrupt:
        logger.info("Pipeline service stopped.")
    except Exception:
        logger.exception("Pipeline service failed. Exiting.")
        sys.exit(1)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
