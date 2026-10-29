import logging
import pathlib
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from pymongo.database import Database

from pipeline_service.pipelines.factory import PipelineFactory
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.jobs.models import Job
from space_weather_analysis.jobs.repository import JobRepository, MongoJobRepository

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Manages the execution of data pipelines based on manual requests or database jobs.

    This class acts as the central controller for the pipeline service. It monitors
    a MongoDB job queue for asynchronous tasks and accepts direct configuration
    dictionaries for immediate execution. It delegates the actual data processing
    to specific pipeline implementations created via a factory.
    """

    POLLING_INTERVAL_SECONDS = 10
    # Define a default directory for data storage relative to the project root
    DATA_BASE_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "data"

    def __init__(self, db: Database) -> None:
        """Initializes the orchestrator with a database connection.

        Args:
            db (Database): The MongoDB database instance used for job management.
        """
        self.db = db
        self.job_repository: JobRepository = MongoJobRepository(db)
        self.catalog_repository: CatalogRepository = MongoCatalogRepository(db)
        # Ensure the download directory exists
        if not self.DATA_BASE_DIR.exists():
            self.DATA_BASE_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created data download directory at {self.DATA_BASE_DIR}")

        # Initialize the factory with the database and the storage path
        self.pipeline_factory = PipelineFactory(self.db, self.DATA_BASE_DIR)

    def run_manual_jobs(self, manual_jobs: Dict[str, Dict[str, Any]]) -> None:
        """Executes a set of pipelines defined by a manual configuration dictionary.

        Args:
            manual_jobs (Dict[str, Dict[str, Any]]): A dictionary mapping source
                identifiers to their specific configuration parameters.
        """
        for source_id, config in manual_jobs.items():
            datasets = config.get("datasets")
            start_time = config.get("start_time")
            end_time = config.get("end_time")

            if not datasets:
                logger.info(f"Skipping manual job for {source_id}: No datasets provided.")
                continue

            logger.info(f"Starting manual job for source: {source_id}")
            # Manual jobs don't have a DB job ID, so we pass None
            self._execute_pipeline(source_id, datasets, start_time, end_time, job_id=None)

    def run_daemon(self) -> None:
        """Starts the continuous job processing loop."""
        logger.info("Pipeline Orchestrator daemon started.")

        while True:
            try:
                job = self.job_repository.claim_next_pending()

                if job:
                    self._process_job(job)
                else:
                    # Wait before the next polling attempt
                    time.sleep(self.POLLING_INTERVAL_SECONDS)

            except Exception as e:
                logger.error(f"Unexpected error in daemon loop: {e}", exc_info=True)
                time.sleep(self.POLLING_INTERVAL_SECONDS)

    def _resolve_source_type(self, datasets: List[str]) -> Optional[str]:
        """Determines the correct pipeline source type for a given list of datasets.

        It queries the master catalog to find the 'type' field associated with the dataset.

        Args:
            datasets: A list of dataset IDs.

        Returns:
            The source type string if found, otherwise None.
        """
        if not datasets:
            return None

        dataset_id = datasets[0]
        sources = self.catalog_repository.get_sources(dataset_id)
        if not sources:
            logger.error(f"Dataset {dataset_id} not found in master catalog or no sources defined for it.")
            return None

        return sources[0].get("type")

    def _process_job(self, job: Job) -> None:
        """Executes the logic for a single retrieved job.

        Args:
            job (Job): The claimed job containing execution details.
        """
        datasets = job.parameters.datasets

        source_type = job.source_id
        if not source_type:
            logger.info(f"Resolving source type for job {job.id}...")
            source_type = self._resolve_source_type(datasets)

        logger.info(f"Processing job {job.id} for source type {source_type}")

        try:
            self._execute_pipeline(source_type, datasets, job.parameters.start_time, job.parameters.end_time, job_id=job.id)

            self.job_repository.finish(job.id, "COMPLETED")
            logger.info(f"Job {job.id} completed successfully.")

        except Exception as e:
            logger.error(f"Job {job.id} failed: {e}", exc_info=True)
            self.job_repository.finish(job.id, "FAILED", str(e))

    def _execute_pipeline(
        self,
        source_id: Optional[str],
        datasets: List[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        job_id: Optional[str],
    ) -> None:
        """Delegates the execution to a specific pipeline instance.

        Args:
            source_id (Optional[str]): The source type identifier used to select the pipeline.
            datasets (List[str]): The list of dataset IDs to process.
            start_time (Optional[datetime]): The start of the time range.
            end_time (Optional[datetime]): The end of the time range.
            job_id (Optional[str]): The ID of the job. If set, progress updates are
                recorded in the job repository; otherwise they are logged.

        Raises:
            ValueError: If the source_id is missing or not supported.
        """
        if not source_id:
            raise ValueError("Source Type could not be resolved or is missing.")

        pipeline = self.pipeline_factory.get_pipeline(source_id)

        # Records the progress of database jobs and logs that of manual jobs
        def progress_callback(progress: float, message: str) -> None:
            if job_id:
                try:
                    self.job_repository.update_progress(job_id, progress, message)
                except Exception as e:
                    logger.warning(f"Failed to update progress for job {job_id}: {e}")
            else:
                # Log to console for manual jobs
                logger.info(f"Manual Job Progress: {progress:.1%} - {message}")

        pipeline.run(datasets, start_time, end_time, progress_callback=progress_callback)
