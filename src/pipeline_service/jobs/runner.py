import logging
import pathlib
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial

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

# Every pipeline reports progress after each chunk or dataset, which takes minutes at most, so a job silent for this
# long hangs.
STALE_JOB_LIMIT = timedelta(minutes=30)


class LaneBlockedError(RuntimeError):
    """A lane thread is still inside a job without progress; a thread cannot be stopped from outside, only the process can end."""


class JobRunner:
    """Runs the pipelines for the download jobs stored in MongoDB by the frontend and the startup configuration.

    Each source type has its own lane that runs its jobs one after the other, so the lanes work in parallel while
    no provider gets more than one request at a time, and an unreachable provider holds up only its own lane.
    A job without source type is first assigned to the lane of its datasets, or split into one job per source type.

    A job ends COMPLETED when at least one of its chunks was ingested or none needed ingesting; the chunks
    that failed are stored with it as missing chunks. It ends FAILED for an invalid request, when every
    chunk failed, or on an unexpected error. A job naming a dataset the catalog does not hold yet waits in the
    queue and is tried again until the catalog wait limit has passed since its creation. A running job without
    progress for the stale job limit ends FAILED, so its feed gets a new job.
    """

    POLLING_INTERVAL_SECONDS = 10
    WATCHDOG_INTERVAL_SECONDS = 60

    def __init__(
        self,
        db: Database,
        data_dir: pathlib.Path,
        pipeline_for: Callable[[SourceType], Pipeline] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        catalog_wait_limit: timedelta = CATALOG_WAIT_LIMIT,
        stale_job_limit: timedelta = STALE_JOB_LIMIT,
    ) -> None:
        """Initializes the job runner.

        Args:
            db: The MongoDB database instance holding jobs, catalog and data.
            data_dir: The directory holding the local dataset files.
            pipeline_for: Builds the pipeline of a source type; the PipelineFactory with its defaults if None.
            clock: Returns the current time in UTC.
            catalog_wait_limit: How long a job naming a dataset the catalog does not hold waits before it fails.
            stale_job_limit: How long a running job may go without progress before it fails.
        """
        self.db = db
        self.clock = clock
        self.catalog_wait_limit = catalog_wait_limit
        self.stale_job_limit = stale_job_limit
        # The ID of the job each lane is running; the lanes write it, the watchdog reads it
        self._running_jobs: dict[SourceType, str] = {}
        self._running_jobs_lock = threading.Lock()
        self.job_repository: JobRepository = MongoJobRepository(db)
        self.catalog_repository: CatalogRepository = MongoCatalogRepository(db)
        self.pipeline_for = pipeline_for if pipeline_for is not None else PipelineFactory(self.db, data_dir).get_pipeline

    def run_daemon(self) -> None:
        """Runs one lane thread per source type and a thread assigning jobs without source type, and watches for stale jobs here.

        Raises:
            LaneBlockedError: If a lane is still inside a job that failed for lack of progress.
        """
        logger.info("Job runner daemon started.")
        # Daemon threads end with the process, as the lanes never return
        for source_type in SourceType:
            lane = threading.Thread(target=self._poll, args=(partial(self.process_next_job, source_type),), name=f"lane-{source_type}", daemon=True)
            lane.start()
        threading.Thread(target=self._poll, args=(self.dispatch_next_job,), name="dispatcher", daemon=True).start()
        while True:
            time.sleep(self.WATCHDOG_INTERVAL_SECONDS)
            self.fail_stale_jobs()

    def fail_stale_jobs(self) -> None:
        """Fails the running jobs without progress for the stale job limit, so their feeds get new jobs.

        Raises:
            LaneBlockedError: If a lane of this runner is still inside one of the failed jobs.
        """
        try:
            failed_jobs = self.job_repository.fail_stale_running(self.clock(), self.stale_job_limit)
        except PyMongoError:
            logger.exception("Checking for jobs without progress failed on a database error")
            return
        for job in failed_jobs:
            logger.error("Job %s of feed %s failed: no progress for %s", job.id, job.feed, self.stale_job_limit)
        failed_ids = {job.id for job in failed_jobs}
        with self._running_jobs_lock:
            blocked_lanes = [source_type for source_type, job_id in self._running_jobs.items() if job_id in failed_ids]
        if blocked_lanes:
            raise LaneBlockedError(f"The lanes {blocked_lanes} are still inside jobs without progress for {self.stale_job_limit}.")

    def run_until_idle(self) -> None:
        """Runs the due jobs one after the other, handing out jobs without source type first, until no job is due.

        Raises:
            pymongo.errors.PyMongoError: If a job cannot be claimed or its end cannot be stored.
        """
        logger.info("Job runner started for the queued jobs.")
        took_job = True
        while took_job:
            took_job = self.dispatch_next_job() or any(self.process_next_job(source_type) for source_type in SourceType)
        logger.info("Job runner finished: no job is due.")

    def _poll(self, take_next_job: Callable[[], bool]) -> None:
        """Takes jobs until the process ends, waiting POLLING_INTERVAL_SECONDS whenever no job was due.

        Args:
            take_next_job: Claims and handles the next due job; returns whether one was due.
        """
        while True:
            try:
                took_job = take_next_job()
            except PyMongoError:
                logger.exception("Claiming or finishing a job failed on a database error")
                took_job = False
            except Exception:
                # A lane that ends stops its source type for good while the process keeps running; the traceback shows the cause
                logger.exception("Unexpected error while claiming or finishing a job, a programming error")
                took_job = False
            if not took_job:
                time.sleep(self.POLLING_INTERVAL_SECONDS)

    def process_next_job(self, source_type: SourceType) -> bool:
        """Claims the oldest pending job of a source type, runs its pipeline and stores how it ended.

        Args:
            source_type: The source type of the lane.

        Returns:
            True if a job was pending.

        Raises:
            pymongo.errors.PyMongoError: If the job cannot be claimed or its end cannot be stored.
        """
        job = self.job_repository.claim_next_pending(self.clock(), source_types=[source_type])
        if job is None:
            return False
        with self._running_jobs_lock:
            self._running_jobs[source_type] = job.id
        try:
            self._process_job(job, source_type)
        finally:
            with self._running_jobs_lock:
                del self._running_jobs[source_type]
        return True

    def dispatch_next_job(self) -> bool:
        """Claims the oldest pending job without source type and hands its datasets to the lanes of their source types.

        The job keeps the datasets of the first source type, and a new job is created for each further source type.

        Returns:
            True if a job was pending.

        Raises:
            pymongo.errors.PyMongoError: If the job cannot be claimed or updated.
        """
        job = self.job_repository.claim_next_pending(self.clock(), source_types=[None])
        if job is None:
            return False
        try:
            groups = self._group_by_source_type(job.parameters.datasets)
        except DatasetNotCataloguedError as error:
            self._wait_for_catalog(job, error)
            return True
        except ValueError as error:
            logger.warning("Job %s rejected: %s", job.id, error)
            self.job_repository.finish(job.id, JobStatus.FAILED, str(error))
            return True

        (first_type, first_datasets), *further_groups = groups.items()
        for source_type, datasets in further_groups:
            new_job_id = self.job_repository.create(datasets, job.parameters.start_time, job.parameters.end_time, source_type, job.feed)
            logger.info("Job %s: datasets %s of source type %s moved to job %s", job.id, datasets, source_type, new_job_id)
        self.job_repository.assign_source_type(job.id, first_type, first_datasets)
        logger.info("Job %s assigned to source type %s: %s", job.id, first_type, first_datasets)
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

    def _process_job(self, job: Job, source_type: SourceType) -> None:
        """Runs the pipeline of a claimed job and stores how it ended.

        Args:
            job: The claimed job.
            source_type: The source type of the job.
        """
        try:
            logger.info("Processing job %s for source type %s: %s", job.id, source_type, job.parameters.datasets)
            report = self._execute_pipeline(source_type, job.parameters.datasets, job.parameters.start_time, job.parameters.end_time, job_id=job.id)
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
        if now - job.created_at >= self.catalog_wait_limit:
            logger.warning("Job %s failed: %s, still after %s", job.id, error, self.catalog_wait_limit)
            self.job_repository.finish(job.id, JobStatus.FAILED, f"{error} The catalog did not list it within {self.catalog_wait_limit}.")
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
