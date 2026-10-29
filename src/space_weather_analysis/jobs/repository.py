from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson.objectid import ObjectId
from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database

from space_weather_analysis.jobs.models import FinalJobStatus, Job, JobParameters


class JobRepository(ABC):
    """Stores the download jobs the frontend requests and the pipeline service executes.

    Frontend and orchestrator read and write jobs only through this interface.
    """

    @abstractmethod
    def create(self, datasets: List[str], start_time: Optional[datetime], end_time: Optional[datetime], source_id: Optional[str] = None) -> str:
        """Creates a pending job.

        Args:
            datasets: The IDs of the datasets to ingest.
            start_time: The start of the requested time range; None lets the pipeline choose.
            end_time: The end of the requested time range; None lets the pipeline choose.
            source_id: The source type of the pipeline to run; None lets the orchestrator resolve it.

        Returns:
            The ID of the new job.
        """

    @abstractmethod
    def get(self, job_id: str) -> Optional[Job]:
        """Reads one job.

        Args:
            job_id: The job ID.

        Returns:
            The job, or None if no job has this ID or the ID is malformed.
        """

    @abstractmethod
    def claim_next_pending(self) -> Optional[Job]:
        """Atomically marks the oldest pending job as running, so no other worker takes it.

        Returns:
            The claimed job with status RUNNING and its start time, or None if no job is pending.
        """

    @abstractmethod
    def update_progress(self, job_id: str, progress: float, message: str) -> None:
        """Records the progress of a running job.

        Args:
            job_id: The job ID.
            progress: The share of the work done, from 0.0 to 1.0.
            message: A description of the current step.
        """

    @abstractmethod
    def finish(self, job_id: str, status: FinalJobStatus, error: Optional[str] = None) -> None:
        """Ends a job.

        Args:
            job_id: The job ID.
            status: COMPLETED or FAILED.
            error: The error description of a failed job.
        """


class MongoJobRepository(JobRepository):
    """Job repository on MongoDB; each job is one document in 'jobs' with an ObjectId as `_id`."""

    COLLECTION = "jobs"

    def __init__(self, db: Database) -> None:
        """Initializes the repository.

        Args:
            db: The MongoDB database instance.
        """
        self.collection = db[self.COLLECTION]

    def create(self, datasets: List[str], start_time: Optional[datetime], end_time: Optional[datetime], source_id: Optional[str] = None) -> str:
        """Creates a pending job.

        Args:
            datasets: The IDs of the datasets to ingest.
            start_time: The start of the requested time range; None lets the pipeline choose.
            end_time: The end of the requested time range; None lets the pipeline choose.
            source_id: The source type of the pipeline to run; None lets the orchestrator resolve it.

        Returns:
            The ID of the new job.
        """
        job = Job(
            id=str(ObjectId()),
            status="PENDING",
            parameters=JobParameters(datasets=datasets, start_time=start_time, end_time=end_time),
            source_id=source_id,
            created_at=datetime.now(timezone.utc),
            progress=0.0,
            message="Job created",
        )
        self.collection.insert_one(job.to_document())
        return job.id

    def get(self, job_id: str) -> Optional[Job]:
        """Reads one job.

        Args:
            job_id: The job ID.

        Returns:
            The job, or None if no job has this ID or the ID is malformed.
        """
        if not ObjectId.is_valid(job_id):
            return None
        document = self.collection.find_one({"_id": ObjectId(job_id)})
        return Job.from_document(document) if document else None

    def claim_next_pending(self) -> Optional[Job]:
        """Atomically marks the oldest pending job as running, so no other worker takes it.

        Returns:
            The claimed job with status RUNNING and its start time, or None if no job is pending.
        """
        # ObjectIds grow with their creation time, so the smallest `_id` is the oldest job
        document = self.collection.find_one_and_update(
            {"status": "PENDING"},
            {"$set": {"status": "RUNNING", "started_at": datetime.now(timezone.utc)}},
            sort=[("_id", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return Job.from_document(document) if document else None

    def update_progress(self, job_id: str, progress: float, message: str) -> None:
        """Records the progress of a running job.

        Args:
            job_id: The job ID.
            progress: The share of the work done, from 0.0 to 1.0.
            message: A description of the current step.

        Raises:
            bson.errors.InvalidId: If the job ID is malformed.
        """
        update = {"progress": progress, "message": message, "updated_at": datetime.now(timezone.utc)}
        self.collection.update_one({"_id": ObjectId(job_id)}, {"$set": update})

    def finish(self, job_id: str, status: FinalJobStatus, error: Optional[str] = None) -> None:
        """Ends a job and records when it ended.

        Args:
            job_id: The job ID.
            status: COMPLETED or FAILED.
            error: The error description of a failed job; not stored if empty.

        Raises:
            bson.errors.InvalidId: If the job ID is malformed.
        """
        update: Dict[str, Any] = {"status": status, "completed_at": datetime.now(timezone.utc)}
        if error:
            update["error"] = error
        self.collection.update_one({"_id": ObjectId(job_id)}, {"$set": update})
