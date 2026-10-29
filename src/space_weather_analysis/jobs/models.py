from datetime import datetime
from typing import Any, Dict, List, Literal, Mapping, Optional

from bson.objectid import ObjectId
from pydantic import BaseModel, Field, field_validator

from space_weather_analysis.util.time_utils import ensure_aware

# Life cycle of a download job: the frontend creates it as PENDING, the orchestrator claims it
# as RUNNING and ends it as COMPLETED or FAILED.
JobStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"]

# The statuses that end a job.
FinalJobStatus = Literal["COMPLETED", "FAILED"]


class JobParameters(BaseModel):
    """What a download job ingests.

    Attributes:
        datasets: The IDs of the datasets to ingest.
        start_time: The start of the requested time range; None lets the pipeline choose.
        end_time: The end of the requested time range; None lets the pipeline choose.
    """

    datasets: List[str] = Field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    @field_validator("start_time", "end_time")
    @classmethod
    def _times_in_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        """Reads naive times as UTC, as MongoDB returns stored times without time zone."""
        return ensure_aware(value) if value is not None else None


class Job(BaseModel):
    """A download job requested by the frontend and executed by the pipeline service.

    Attributes:
        id: The job ID, the text of the MongoDB ObjectId stored as `_id`.
        status: The current status.
        parameters: What the job ingests.
        source_id: The source type of the pipeline to run; None lets the orchestrator resolve it from the master catalog.
        created_at: When the job was created.
        started_at: When the orchestrator claimed the job.
        updated_at: When the progress was last reported.
        completed_at: When the job ended.
        progress: The share of the work done, from 0.0 to 1.0.
        message: The latest progress message.
        error: The error description of a failed job.
    """

    id: str
    status: JobStatus
    parameters: JobParameters = Field(default_factory=JobParameters)
    source_id: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    progress: float = 0.0
    message: Optional[str] = None
    error: Optional[str] = None

    @field_validator("created_at", "started_at", "updated_at", "completed_at")
    @classmethod
    def _times_in_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        """Reads naive times as UTC, as MongoDB returns stored times without time zone."""
        return ensure_aware(value) if value is not None else None

    def to_document(self) -> Dict[str, Any]:
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
