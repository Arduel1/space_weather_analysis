import logging
import pathlib
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo.database import Database

from src.pipeline_service.pipelines.factory import PipelineFactory

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """
    Manages the execution of data pipelines based on manual requests or database jobs.

    This class acts as the central controller for the pipeline service. It monitors
    a MongoDB job queue for asynchronous tasks and accepts direct configuration
    dictionaries for immediate execution. It delegates the actual data processing
    to specific pipeline implementations created via a factory.
    """

    JOBS_COLLECTION = "jobs"
    CATALOG_COLLECTION = "master_catalog"
    POLLING_INTERVAL_SECONDS = 10
    # Define a default directory for data storage relative to the project root
    DATA_BASE_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "data"

    def __init__(self, db: Database) -> None:
        """
        Initializes the orchestrator with a database connection.

        Args:
            db (Database): The MongoDB database instance used for job management.
        """
        self.db = db
        # Ensure the download directory exists
        if not self.DATA_BASE_DIR.exists():
            self.DATA_BASE_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created data download directory at {self.DATA_BASE_DIR}")

        # Initialize the factory with the database and the storage path
        self.pipeline_factory = PipelineFactory(self.db, self.DATA_BASE_DIR)

    def run_manual_jobs(self, manual_jobs: Dict[str, Dict[str, Any]]) -> None:
        """
        Executes a set of pipelines defined by a manual configuration dictionary.

        This method iterates through the provided source configurations and triggers
        the corresponding pipelines immediately. It is primarily used for ad-hoc
        runs or testing specific datasets without scheduling a database job.

        Args:
            manual_jobs (Dict[str, Dict[str, Any]]): A dictionary mapping source
                identifiers to their specific configuration parameters like time
                range and dataset lists.
        """
        for source_id, config in manual_jobs.items():
            datasets = config.get("datasets")
            start_time = config.get("start_time")
            end_time = config.get("end_time")

            if not datasets:
                logger.info(f"Skipping manual job for {source_id}: No datasets provided.")
                continue

            logger.info(f"Starting manual job for source: {source_id}")
            self._execute_pipeline(source_id, datasets, start_time, end_time)

    def run_daemon(self) -> None:
        """
        Starts the continuous job processing loop.

        This method polls the database for pending jobs indefinitely. When a job
        is found, it locks the job by updating its status and proceeds to execute
        the requested pipeline. It handles both success and failure states by
        updating the job record accordingly.
        """
        logger.info("Pipeline Orchestrator daemon started.")

        while True:
            try:
                job = self._fetch_next_pending_job()

                if job:
                    self._process_job(job)
                else:
                    # Wait before the next polling attempt to reduce database load
                    time.sleep(self.POLLING_INTERVAL_SECONDS)

            except Exception as e:
                logger.error(f"Unexpected error in daemon loop: {e}", exc_info=True)
                # Sleep briefly to avoid rapid error looping
                time.sleep(self.POLLING_INTERVAL_SECONDS)

    def _fetch_next_pending_job(self) -> Optional[Dict[str, Any]]:
        """
        Retrieves and locks the next pending job from the database.

        This method looks for a document with specific status criteria and
        atomically updates it to prevent other instances from picking up the
        same job.

        Returns:
            Optional[Dict[str, Any]]: The job document if found and locked,
            otherwise None.
        """
        collection = self.db[self.JOBS_COLLECTION]

        # Atomically find a pending job and set it to running
        return collection.find_one_and_update(
            {"status": "PENDING"},
            {
                "$set": {
                    "status": "RUNNING",
                    "started_at": datetime.now(timezone.utc),
                }
            },
            return_document=True,
        )

    def _resolve_source_type(self, datasets: List[str]) -> Optional[str]:
        """
        Determines the correct pipeline source type for a given list of datasets.

        It queries the master catalog to find the 'type' field (e.g., 'HAPI', 'FIDO_CDAWEB')
        associated with the dataset.

        Args:
            datasets: A list of dataset IDs.

        Returns:
            The source type string if found, otherwise None.
        """
        if not datasets:
            return None

        # We assume all datasets in a single job belong to the same source.
        # We take the first one to resolve the source.
        dataset_id = datasets[0]

        catalog_entry = self.db[self.CATALOG_COLLECTION].find_one({"dataset_id": dataset_id})
        if not catalog_entry:
            logger.error(f"Dataset {dataset_id} not found in master catalog.")
            return None

        sources = catalog_entry.get("sources", [])
        if not sources:
            logger.error(f"No sources defined for dataset {dataset_id}.")
            return None

        # We prefer the first source listed (usually the primary one)
        # The 'type' field corresponds to the factory keys (e.g. 'HAPI')
        return sources[0].get("type")

    def _process_job(self, job: Dict[str, Any]) -> None:
        """
        Executes the logic for a single retrieved job.

        Args:
            job (Dict[str, Any]): The job document containing execution details.
        """
        job_id = job.get("_id")
        params = job.get("parameters", {})
        datasets = params.get("datasets", [])

        # The job might explicitly specify a source_id (legacy), or we resolve it.
        # 'source_id' in the job context refers to the Pipeline Factory Type.
        source_type = job.get("source_id")

        if not source_type:
            logger.info(f"Resolving source type for job {job_id}...")
            source_type = self._resolve_source_type(datasets)

        logger.info(f"Processing job {job_id} for source type {source_type}")

        try:
            # Extract parameters safely
            start_time = params.get("start_time")
            end_time = params.get("end_time")

            self._execute_pipeline(source_type, datasets, start_time, end_time)

            self._update_job_status(job_id, "COMPLETED")
            logger.info(f"Job {job_id} completed successfully.")

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}", exc_info=True)
            self._update_job_status(job_id, "FAILED", str(e))

    def _update_job_status(self, job_id: Any, status: str, error_message: Optional[str] = None) -> None:
        """
        Updates the final status of a job in the database.

        Args:
            job_id (Any): The unique identifier of the job.
            status (str): The new status string (e.g., COMPLETED, FAILED).
            error_message (Optional[str]): An optional error description if failed.
        """
        update_doc = {"status": status, "completed_at": datetime.now(timezone.utc)}

        if error_message:
            update_doc["error"] = error_message

        self.db[self.JOBS_COLLECTION].update_one({"_id": job_id}, {"$set": update_doc})

    def _execute_pipeline(
        self,
        source_id: Optional[str],
        datasets: List[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> None:
        """
        Delegates the execution to a specific pipeline instance.

        Args:
            source_id (Optional[str]): The identifier for the data source (e.g., HAPI).
            datasets (List[str]): The list of dataset IDs to process.
            start_time (Optional[datetime]): The start of the time range.
            end_time (Optional[datetime]): The end of the time range.

        Raises:
            ValueError: If the source_id is missing or not supported.
        """
        if not source_id:
            raise ValueError("Source Type could not be resolved or is missing.")

        pipeline = self.pipeline_factory.get_pipeline(source_id)
        pipeline.run(datasets, start_time, end_time)
