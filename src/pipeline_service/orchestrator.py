import logging
import pathlib
import time
from datetime import datetime
from typing import Any

from pymongo.database import Database
from pymongo.errors import PyMongoError

from pipeline_service.pipelines.factory import PipelineFactory
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.jobs.models import Job, JobStatus
from space_weather_analysis.jobs.repository import JobRepository, MongoJobRepository

logger = logging.getLogger(__name__)

# Requests the pipelines reject before ingesting anything: invalid parameters or a missing local file.
INVALID_REQUEST_ERRORS = (ValueError, FileNotFoundError)


class PipelineOrchestrator:
    """Runs the pipelines for manual configurations and for the download jobs the frontend stores in MongoDB.

    A job ends COMPLETED when at least one of its chunks was ingested or none needed ingesting; the chunks
    that failed are stored with it as missing chunks. It ends FAILED for an invalid request, when every
    chunk failed, or on an unexpected error.
    """

    POLLING_INTERVAL_SECONDS = 10
    # The data directory of the project, holding the local dataset files
    DATA_BASE_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "data"

    def __init__(self, db: Database, data_dir: pathlib.Path = DATA_BASE_DIR) -> None:
        """Initializes the orchestrator.

        Args:
            db: The MongoDB database instance holding jobs, catalog and data.
            data_dir: The directory holding the local dataset files.
        """
        self.db = db
        self.job_repository: JobRepository = MongoJobRepository(db)
        self.catalog_repository: CatalogRepository = MongoCatalogRepository(db)
        self.pipeline_factory = PipelineFactory(self.db, data_dir)

    def run_manual_jobs(self, manual_jobs: dict[SourceType, dict[str, Any]]) -> None:
        """Runs the pipelines of a manual configuration one after the other; a failing one does not stop the others.

        Args:
            manual_jobs: The configuration per source type, with 'datasets' and optionally 'start_time' and 'end_time'.
        """
        for source_type, config in manual_jobs.items():
            datasets = config.get("datasets")
            if not datasets:
                logger.info("Skipping manual job for %s: no datasets given.", source_type)
                continue
            logger.info("Starting manual job for %s", source_type)
            try:
                report = self._execute_pipeline(source_type, datasets, config.get("start_time"), config.get("end_time"), job_id=None)
            except INVALID_REQUEST_ERRORS as error:
                logger.warning("Manual job for %s rejected: %s", source_type, error)
                continue
            except Exception:
                # A manual job must not keep the service from serving the job queue
                logger.exception("Manual job for %s failed", source_type)
                continue
            logger.info("Manual job for %s done: %d chunks tried, %d missing.", source_type, report.chunks_total, len(report.gaps))

    def run_daemon(self) -> None:
        """Claims and runs pending jobs until the process ends, polling every POLLING_INTERVAL_SECONDS."""
        logger.info("Pipeline Orchestrator daemon started.")
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
        job = self.job_repository.claim_next_pending()
        if job is None:
            return False
        self._process_job(job)
        return True

    def _resolve_source_type(self, datasets: list[str]) -> SourceType:
        """Reads the source type of the first dataset from the master catalog.

        Args:
            datasets: The dataset IDs of the job.

        Returns:
            The source type of the first source of the first dataset.

        Raises:
            ValueError: If the job has no dataset, the dataset has no source or its source type is unknown.
        """
        if not datasets:
            raise ValueError("The job names no dataset.")
        sources = self.catalog_repository.get_sources(datasets[0])
        if not sources:
            raise ValueError(f"Dataset {datasets[0]} has no source in the master catalog.")
        return SourceType(sources[0]["type"])

    def _process_job(self, job: Job) -> None:
        """Runs one claimed job and stores how it ended.

        Args:
            job: The claimed job.
        """
        try:
            source_type = job.source_type or self._resolve_source_type(job.parameters.datasets)
            logger.info("Processing job %s for source type %s", job.id, source_type)
            report = self._execute_pipeline(source_type, job.parameters.datasets, job.parameters.start_time, job.parameters.end_time, job_id=job.id)
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
        pipeline = self.pipeline_factory.get_pipeline(source_type)

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
