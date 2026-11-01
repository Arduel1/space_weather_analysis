from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from space_weather_analysis.util.time_utils import ensure_aware


class CoverageInterval(BaseModel):
    """A time range in which every day holds at least one stored document.

    Attributes:
        start: The start of the first day with documents (inclusive), at 00:00 UTC.
        end: The start of the first day after the range without documents (exclusive), at 00:00 UTC.
    """

    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def _times_in_utc(cls, value: datetime) -> datetime:
        """Reads naive times as UTC, as a MongoDB client without tz_aware returns stored times without time zone."""
        return ensure_aware(value)


class StoredCoverage(BaseModel):
    """The time ranges of one dataset that are stored in the database and can be queried.

    Attributes:
        dataset_id: The dataset ID as named in the master catalog.
        collection: The MongoDB collection holding the data documents of the dataset.
        first_timestamp: The time of the earliest stored document; None if nothing is stored.
        last_timestamp: The time of the latest stored document; None if nothing is stored.
        document_count: The number of stored documents.
        intervals: The ranges of consecutive days with documents, sorted by time.
        bucket: The size of the time buckets the intervals are built from, as ISO 8601 duration.
        updated_at: When the coverage was computed.
    """

    dataset_id: str
    collection: str
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    document_count: int
    intervals: list[CoverageInterval] = Field(default_factory=list)
    bucket: str
    updated_at: datetime

    @field_validator("first_timestamp", "last_timestamp", "updated_at")
    @classmethod
    def _times_in_utc(cls, value: datetime | None) -> datetime | None:
        """Reads naive times as UTC, as a MongoDB client without tz_aware returns stored times without time zone."""
        return ensure_aware(value) if value is not None else None

    def to_document(self) -> dict[str, Any]:
        """Converts the coverage into a MongoDB document with the dataset ID as `_id`.

        Returns:
            The document; times stay datetime objects, so MongoDB stores them as dates.
        """
        return {"_id": self.dataset_id, **self.model_dump()}

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "StoredCoverage":
        """Builds the coverage from a document written by to_document.

        Args:
            document: The stored document.

        Returns:
            The coverage, with naive stored times read as UTC.

        Raises:
            pydantic.ValidationError: If the document does not match the schema.
        """
        fields = {key: value for key, value in document.items() if key != "_id"}
        return cls.model_validate(fields)
