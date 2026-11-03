import logging
import pathlib
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pymongo.database import Database
from pymongo.errors import PyMongoError

from pipeline_service.pipelines.base import Pipeline
from pipeline_service.pipelines.factory import PipelineFactory
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.jobs.models import Job, JobStatus
from space_weather_analysis.jobs.repository import JobRepository, MongoJobRepository
from space_weather_analysis.util.error_handling import DatasetNotCataloguedError

logger = logging.getLogger(__name__)

# Requests the pipelines reject before ingesting anything: invalid parameters or a missing local file.
INVALID_REQUEST_ERRORS = (ValueError, FileNotFoundError)

# The metadata worker builds the catalog within minutes of a start, so a job naming a dataset still unknown after
# this wait names one that does not exist.
CATALOG_WAIT_LIMIT = timedelta(hours=1)
# The wait before a job for a dataset not yet in the catalog is tried again.
CATALOG_RETRY_DELAY = timedelta(minutes=1)


class JobRunner:
    """Runs the pipelines for the download jobs stored in MongoDB by the frontend and the startup configuration.

    A job ends COMPLETED when at least one of its chunks was ingested or none needed ingesting; the chunks
    that failed are stored with it as missing chunks. It ends FAILED for an invalid request, when every
    chunk failed, or on an unexpected error. A job naming a dataset the catalog does not hold yet waits in the
    queue and is tried again until CATALOG_WAIT_LIMIT has passed since its creation.
    """

    POLLING_INTERVAL_SECONDS = 10

    def __init__(
        self,
        db: Database,
        data_dir: pathlib.Path,
        pipeline_for: Callable[[SourceType], Pipeline] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initializes the job runner.

        Args:
            db: The MongoDB database instance holding jobs, catalog and data.
            data_dir: The directory holding the local dataset files.
            pipeline_for: Builds the pipeline of a source type; the PipelineFactory with its defaults if None.
            clock: Returns the current time in UTC.
        """
        self.db = db
        self.clock = clock
        self.job_repository: JobRepository = MongoJobRepository(db)
        self.catalog_repository: CatalogRepository = MongoCatalogRepository(db)
        self.pipeline_for = pipeline_for if pipeline_for is not None else PipelineFactory(self.db, data_dir).get_pipeline

    def run_daemon(self) -> None:
        """Claims and runs pending jobs until the process ends, polling every POLLING_INTERVAL_SECONDS."""
        logger.info("Job runner daemon started.")
        while True:
            try:
                processed_job = self.process_next_job()
            except PyMongoError:
                logger.exception("Claiming or finishing a job failed")
                processed_job = False
            if not processed_job:
                time.sleep(self.POLLING_INTERVAL_SECONDS)

    def process_next_job(self) -> bool:
        """Claims the oldest pending job, runs it and stores how it ended.

        Returns:
            True if a job was pending.

        Raises:
            pymongo.errors.PyMongoError: If the job cannot be claimed or its end cannot be stored.
        """
        job = self.job_repository.claim_next_pending(self.clock())
        if job is None:
            return False
        self._process_job(job)
        return True

    def _group_by_source_type(self, datasets: list[str]) -> dict[SourceType, list[str]]:
        """Groups the datasets of a job by the source type of their first source in the master catalog.

        Args:
            datasets: The dataset IDs of the job.

        Returns:
            The dataset IDs per source type, in the order of the job.

        Raises:
            ValueError: If the job has no dataset or a source type is unknown.
            DatasetNotCataloguedError: If a dataset has no source in the master catalog.
        """
        if not datasets:
            raise ValueError("The job names no dataset.")
        groups: dict[SourceType, list[str]] = {}
        for dataset_id in datasets:
            sources = self.catalog_repository.get_sources(dataset_id)
            if not sources:
                raise DatasetNotCataloguedError(f"Dataset {dataset_id} has no source in the master catalog.")
            groups.setdefault(SourceType(sources[0]["type"]), []).append(dataset_id)
        return groups

    def _process_job(self, job: Job) -> None:
        """Runs one claimed job and stores how it ended.

        Args:
            job: The claimed job.
        """
        try:
            if job.source_type is not None:
                groups = {job.source_type: job.parameters.datasets}
            else:
                groups = self._group_by_source_type(job.parameters.datasets)
            report = IngestionReport()
            for source_type, datasets in groups.items():
                logger.info("Processing job %s for source type %s: %s", job.id, source_type, datasets)
                report.merge(self._execute_pipeline(source_type, datasets, job.parameters.start_time, job.parameters.end_time, job_id=job.id))
        except DatasetNotCataloguedError as error:
            self._wait_for_catalog(job, error)
            return
        except INVALID_REQUEST_ERRORS as error:
            logger.warning("Job %s rejected: %s", job.id, error)
            self.job_repository.finish(job.id, JobStatus.FAILED, str(error))
            return
        except Exception as error:
            # The daemon must outlive a failing job; the traceback shows the cause
            logger.exception("Job %s failed", job.id)
            self.job_repository.finish(job.id, JobStatus.FAILED, f"{type(error).__name__}: {error}")
            return

        if report.all_failed:
            logger.warning("Job %s failed: all %d chunks failed.", job.id, report.chunks_total)
            self.job_repository.finish(job.id, JobStatus.FAILED, f"All {report.chunks_total} chunks failed.", report.gaps)
            return
        logger.info("Job %s completed: %d chunks tried, %d missing.", job.id, report.chunks_total, len(report.gaps))
        self.job_repository.finish(job.id, JobStatus.COMPLETED, missing_chunks=report.gaps)

    def _wait_for_catalog(self, job: Job, error: DatasetNotCataloguedError) -> None:
        """Puts a job naming a dataset not yet in the catalog back into the queue, or fails it once the wait is over.

        Args:
            job: The claimed job.
            error: The error naming the missing dataset.
        """
        now = self.clock()
        if now - job.created_at >= CATALOG_WAIT_LIMIT:
            logger.warning("Job %s failed: %s, still after %s", job.id, error, CATALOG_WAIT_LIMIT)
            self.job_repository.finish(job.id, JobStatus.FAILED, f"{error} The catalog did not list it within {CATALOG_WAIT_LIMIT}.")
            return
        logger.info("Job %s waits for the catalog: %s", job.id, error)
        self.job_repository.requeue(job.id, now + CATALOG_RETRY_DELAY, str(error))

    def _execute_pipeline(
        self, source_type: SourceType, datasets: list[str], start_time: datetime | None, end_time: datetime | None, job_id: str | None
    ) -> IngestionReport:
        """Runs the pipeline of a source type and reports its progress.

        Args:
            source_type: The source type selecting the pipeline.
            datasets: The dataset IDs to ingest.
            start_time: The start of the time range.
            end_time: The end of the time range.
            job_id: The ID of the job whose progress is stored; None to log the progress.

        Returns:
            The report of the pipeline run.
        """
        pipeline = self.pipeline_for(source_type)

        def progress_callback(progress: float, message: str) -> None:
            """Stores the progress of a job or logs that of a manual run."""
            if job_id is None:
                logger.info("Manual job progress: %.1f%% - %s", progress * 100, message)
                return
            try:
                self.job_repository.update_progress(job_id, progress, message)
            except PyMongoError as error:
                logger.warning("Failed to update the progress of job %s: %s", job_id, error)

        return pipeline.run(datasets, start_time, end_time, progress_callback=progress_callback)
