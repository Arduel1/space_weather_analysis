from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from bson.objectid import ObjectId
from pydantic import BaseModel, Field

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.util.time_utils import UtcDatetime


class JobStatus(StrEnum):
    """The life cycle of a download job.

    The frontend creates a job as PENDING, the job runner claims it as RUNNING and ends it as COMPLETED or FAILED.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# The statuses that end a job.
FinalJobStatus = Literal[JobStatus.COMPLETED, JobStatus.FAILED]


class JobParameters(BaseModel):
    """What a download job ingests.

    Attributes:
        datasets: The IDs of the datasets to ingest.
        start_time: The start of the requested time range; None lets the pipeline choose.
        end_time: The end of the requested time range; None lets the pipeline choose.
    """

    datasets: list[str] = Field(default_factory=list)
    start_time: UtcDatetime | None = None
    end_time: UtcDatetime | None = None


class ChunkGap(BaseModel):
    """A time range of a dataset that a run could not ingest, so the stored data lack it until a later run fetches it.

    Attributes:
        dataset_id: The dataset ID.
        start: The start of the range (inclusive).
        stop: The end of the range (exclusive).
        reason: Why the range was not ingested.
    """

    dataset_id: str
    start: UtcDatetime
    stop: UtcDatetime
    reason: str


class Job(BaseModel):
    """A download job requested by the frontend and executed by the pipeline service.

    Attributes:
        id: The job ID, the text of the MongoDB ObjectId stored as `_id`.
        status: The current status.
        parameters: What the job ingests.
        source_type: The source type of the pipeline to run; None lets the job runner resolve it from the master catalog.
        feed: The name of the configured feed that queued the job; None for a job requested in the frontend.
        created_at: When the job was created.
        started_at: When the job runner claimed the job.
        updated_at: When the progress was last reported.
        completed_at: When the job ended.
        progress: The share of the work done, from 0.0 to 1.0.
        message: The latest progress message.
        error: The error description of a failed job.
        missing_chunks: The time ranges a completed job could not ingest.
        retry_at: The earliest time a job put back into the queue is claimed again; None if it may run at once.
    """

    id: str
    status: JobStatus
    parameters: JobParameters = Field(default_factory=JobParameters)
    source_type: SourceType | None = None
    feed: str | None = None
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None
    completed_at: UtcDatetime | None = None
    progress: float = 0.0
    message: str | None = None
    error: str | None = None
    missing_chunks: list[ChunkGap] = Field(default_factory=list)
    retry_at: UtcDatetime | None = None

    def to_document(self) -> dict[str, Any]:
        """Converts the job into its MongoDB document.

        Returns:
            The document with the ID as ObjectId in `_id`; fields without value are left out.
        """
        return {"_id": ObjectId(self.id), **self.model_dump(exclude={"id"}, exclude_none=True)}

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "Job":
        """Builds a job from its MongoDB document.

        Args:
            document: The stored document with the ObjectId in `_id`.

        Returns:
            The job.

        Raises:
            pydantic.ValidationError: If the document does not match the job schema.
        """
        fields = {key: value for key, value in document.items() if key != "_id"}
        return cls.model_validate({**fields, "id": str(document["_id"])})


class FeedJobSummary(BaseModel):
    """How the jobs of one configured feed went: its newest job and the outcomes within a time span.

    Attributes:
        feed: The name of the feed.
        latest: The newest job of the feed, of any age.
        runs: The jobs of the feed created within the span.
        completed: The jobs of the span that ended COMPLETED without missing chunks.
        with_gaps: The jobs of the span that ended COMPLETED with missing chunks.
        failed: The jobs of the span that ended FAILED.
    """

    feed: str
    latest: Job
    runs: int = 0
    completed: int = 0
    with_gaps: int = 0
    failed: int = 0
