from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from bson.objectid import ObjectId
from pymongo import ASCENDING, ReturnDocument
from pymongo.database import Database

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.jobs.models import ChunkGap, FinalJobStatus, Job, JobParameters, JobStatus


class JobRepository(ABC):
    """Stores the download jobs the frontend requests and the pipeline service executes.

    Frontend and job runner read and write jobs only through this interface.
    """

    @abstractmethod
    def create(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        source_type: SourceType | None = None,
        feed: str | None = None,
    ) -> str:
        """Creates a pending job.

        Args:
            datasets: The IDs of the datasets to ingest.
            start_time: The start of the requested time range; None lets the pipeline choose.
            end_time: The end of the requested time range; None lets the pipeline choose.
            source_type: The source type of the pipeline to run; None lets the job runner resolve it.
            feed: The name of the configured feed that queues the job; None for a job requested in the frontend.

        Returns:
            The ID of the new job.
        """

    @abstractmethod
    def has_open_job(self, feed: str) -> bool:
        """Tells whether a job of a feed is still pending or running.

        Args:
            feed: The name of the configured feed.

        Returns:
            True if a job of the feed has not ended yet.
        """

    @abstractmethod
    def get(self, job_id: str) -> Job | None:
        """Reads one job.

        Args:
            job_id: The job ID.

        Returns:
            The job, or None if no job has this ID or the ID is malformed.
        """

    @abstractmethod
    def claim_next_pending(self, now: datetime | None = None, source_types: Sequence[SourceType | None] | None = None) -> Job | None:
        """Atomically marks the oldest pending job whose retry time has come as running, so no other worker takes it.

        Args:
            now: The current time; the system time if None.
            source_types: The source types of the jobs to consider, None standing for jobs without source type; every job if None.

        Returns:
            The claimed job with status RUNNING and its start time, or None if no job is due.
        """

    @abstractmethod
    def assign_source_type(self, job_id: str, source_type: SourceType, datasets: list[str]) -> None:
        """Puts a claimed job back into the queue with the source type of its pipeline and the datasets of that type.

        Args:
            job_id: The job ID.
            source_type: The source type of the pipeline to run.
            datasets: The datasets of the job that belong to the source type.
        """

    @abstractmethod
    def requeue(self, job_id: str, retry_at: datetime, message: str) -> None:
        """Puts a claimed job back into the queue until its retry time.

        Args:
            job_id: The job ID.
            retry_at: The earliest time the job is claimed again.
            message: Why the job waits.
        """

    @abstractmethod
    def requeue_running(self) -> int:
        """Puts every running job back into the queue, for a job runner starting after an interrupted run.

        Returns:
            The number of requeued jobs.
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
    def finish(self, job_id: str, status: FinalJobStatus, error: str | None = None, missing_chunks: Sequence[ChunkGap] = ()) -> None:
        """Ends a job.

        Args:
            job_id: The job ID.
            status: COMPLETED or FAILED.
            error: The error description of a failed job.
            missing_chunks: The time ranges the job could not ingest.
        """


class MongoJobRepository(JobRepository):
    """Job repository on MongoDB; each job is one document in 'jobs' with an ObjectId as `_id`."""

    COLLECTION = "jobs"

    def __init__(self, db: Database) -> None:
        """Initializes the repository; the index is created before the first claim.

        Args:
            db: The MongoDB database instance.
        """
        self.collection = db[self.COLLECTION]
        self._index_ensured = False

    def _ensure_index(self) -> None:
        """Creates the index for finding the oldest pending job, once per instance."""
        if self._index_ensured:
            return
        self.collection.create_index([("status", ASCENDING), ("_id", ASCENDING)])
        self._index_ensured = True

    def create(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        source_type: SourceType | None = None,
        feed: str | None = None,
    ) -> str:
        """Creates a pending job.

        Args:
            datasets: The IDs of the datasets to ingest.
            start_time: The start of the requested time range; None lets the pipeline choose.
            end_time: The end of the requested time range; None lets the pipeline choose.
            source_type: The source type of the pipeline to run; None lets the job runner resolve it.
            feed: The name of the configured feed that queues the job; None for a job requested in the frontend.

        Returns:
            The ID of the new job.
        """
        job = Job(
            id=str(ObjectId()),
            status=JobStatus.PENDING,
            parameters=JobParameters(datasets=datasets, start_time=start_time, end_time=end_time),
            source_type=source_type,
            feed=feed,
            created_at=datetime.now(UTC),
            progress=0.0,
            message="Job created",
        )
        self.collection.insert_one(job.to_document())
        return job.id

    def has_open_job(self, feed: str) -> bool:
        """Tells whether a job of a feed is still pending or running.

        Args:
            feed: The name of the configured feed.

        Returns:
            True if a job of the feed has not ended yet.
        """
        return self.collection.find_one({"feed": feed, "status": {"$in": [JobStatus.PENDING, JobStatus.RUNNING]}}, {"_id": 1}) is not None

    def get(self, job_id: str) -> Job | None:
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

    def claim_next_pending(self, now: datetime | None = None, source_types: Sequence[SourceType | None] | None = None) -> Job | None:
        """Atomically marks the oldest pending job whose retry time has come as running, so no other worker takes it.

        Args:
            now: The current time; the system time if None.
            source_types: The source types of the jobs to consider, None standing for jobs without source type; every job if None.

        Returns:
            The claimed job with status RUNNING and its start time, or None if no job is due.
        """
        self._ensure_index()
        claimed_at = now or datetime.now(UTC)
        due: dict[str, Any] = {"status": JobStatus.PENDING, "$or": [{"retry_at": {"$exists": False}}, {"retry_at": {"$lte": claimed_at}}]}
        if source_types is not None:
            # None in $in also matches documents without the field
            due["source_type"] = {"$in": list(source_types)}
        # ObjectIds grow with their creation time, so the smallest `_id` is the oldest job
        document = self.collection.find_one_and_update(
            due,
            {"$set": {"status": JobStatus.RUNNING, "started_at": claimed_at}},
            sort=[("_id", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return Job.from_document(document) if document else None

    def requeue(self, job_id: str, retry_at: datetime, message: str) -> None:
        """Puts a claimed job back into the queue until its retry time.

        Args:
            job_id: The job ID.
            retry_at: The earliest time the job is claimed again.
            message: Why the job waits.

        Raises:
            bson.errors.InvalidId: If the job ID is malformed.
        """
        update = {"$set": {"status": JobStatus.PENDING, "retry_at": retry_at, "message": message}, "$unset": {"started_at": ""}}
        self.collection.update_one({"_id": ObjectId(job_id)}, update)

    def assign_source_type(self, job_id: str, source_type: SourceType, datasets: list[str]) -> None:
        """Puts a claimed job back into the queue with the source type of its pipeline and the datasets of that type.

        Args:
            job_id: The job ID.
            source_type: The source type of the pipeline to run.
            datasets: The datasets of the job that belong to the source type.

        Raises:
            bson.errors.InvalidId: If the job ID is malformed.
        """
        update = {"$set": {"status": JobStatus.PENDING, "source_type": source_type, "parameters.datasets": datasets}, "$unset": {"started_at": ""}}
        self.collection.update_one({"_id": ObjectId(job_id)}, update)

    def requeue_running(self) -> int:
        """Puts every running job back into the queue, for a job runner starting after an interrupted run.

        Returns:
            The number of requeued jobs.
        """
        update = {"$set": {"status": JobStatus.PENDING, "message": "Requeued after an interrupted run"}, "$unset": {"started_at": ""}}
        return self.collection.update_many({"status": JobStatus.RUNNING}, update).modified_count

    def update_progress(self, job_id: str, progress: float, message: str) -> None:
        """Records the progress of a running job.

        Args:
            job_id: The job ID.
            progress: The share of the work done, from 0.0 to 1.0.
            message: A description of the current step.

        Raises:
            bson.errors.InvalidId: If the job ID is malformed.
        """
        update = {"progress": progress, "message": message, "updated_at": datetime.now(UTC)}
        self.collection.update_one({"_id": ObjectId(job_id)}, {"$set": update})

    def finish(self, job_id: str, status: FinalJobStatus, error: str | None = None, missing_chunks: Sequence[ChunkGap] = ()) -> None:
        """Ends a job and records when it ended.

        Args:
            job_id: The job ID.
            status: COMPLETED or FAILED.
            error: The error description of a failed job; not stored if empty.
            missing_chunks: The time ranges the job could not ingest; not stored if empty.

        Raises:
            bson.errors.InvalidId: If the job ID is malformed.
        """
        update: dict[str, Any] = {"status": status, "completed_at": datetime.now(UTC)}
        if status == JobStatus.COMPLETED:
            update["progress"] = 1.0
        if error:
            update["error"] = error
        if missing_chunks:
            update["missing_chunks"] = [gap.model_dump() for gap in missing_chunks]
        self.collection.update_one({"_id": ObjectId(job_id)}, {"$set": update})
