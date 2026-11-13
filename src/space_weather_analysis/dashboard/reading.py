from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import pandas as pd

from space_weather_analysis.dashboard.labels import Metadata
from space_weather_analysis.dashboard.panels.base import collection_of
from space_weather_analysis.jobs.models import FeedJobSummary
from space_weather_analysis.jobs.repository import JobRepository
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.timeseries.repository import TimeSeriesRepository


class DashboardReader(Protocol):
    """The reads the panels build their payloads from; datasets are named by their dataset ID."""

    def latest(self, dataset: str, fields: tuple[str, ...]) -> tuple[datetime, dict[str, Any]] | None:
        """Returns the time stamp and the given data fields of the newest document of a dataset; None without documents."""
        ...

    def recent(self, dataset: str, span: timedelta, fields: tuple[str, ...]) -> pd.DataFrame:
        """Returns the documents of a span ending with the newest document, with a UTC DatetimeIndex; empty without documents."""
        ...

    def metadata(self, dataset: str) -> Metadata:
        """Returns the metadata record of a dataset as JSON-compatible dictionary; empty if none is stored."""
        ...

    def feed_summaries(self, since: datetime) -> list[FeedJobSummary]:
        """Returns the job summary of every feed with a job, counting the outcomes since a time."""
        ...


# Spans from which a read is kept for LONG_READ_TTL, as long histories change little between two exports
LONG_READ_SPAN = timedelta(days=30)
# How long a read of a long span is kept before it is read again
LONG_READ_TTL = timedelta(hours=1)


class RepositoryReader:
    """Reads the panel data from the core repositories; reads of long spans are kept for LONG_READ_TTL, database errors propagate."""

    def __init__(self, timeseries: TimeSeriesRepository, metadata: MetadataRepository, jobs: JobRepository) -> None:
        """Keeps the repositories to read from.

        Args:
            timeseries: The repository of the measured values.
            metadata: The repository of the metadata records.
            jobs: The repository of the jobs.
        """
        self._timeseries = timeseries
        self._metadata = metadata
        self._jobs = jobs
        self._long_reads: dict[tuple[str, timedelta, tuple[str, ...]], tuple[datetime, pd.DataFrame]] = {}

    def latest(self, dataset: str, fields: tuple[str, ...]) -> tuple[datetime, dict[str, Any]] | None:
        """Returns the time stamp and the given data fields of the newest document of a dataset; None without documents."""
        return self._timeseries.read_latest(collection_of(dataset), fields)

    def recent(self, dataset: str, span: timedelta, fields: tuple[str, ...]) -> pd.DataFrame:
        """Returns the documents of a span ending with the newest document, with a UTC DatetimeIndex; empty without documents.

        A kept read is the same frame for every caller, so callers must not change a returned frame in place.
        """
        if span < LONG_READ_SPAN:
            return self._read_recent(dataset, span, fields)
        key = (dataset, span, fields)
        now = datetime.now(UTC)
        kept = self._long_reads.get(key)
        if kept is None or now - kept[0] > LONG_READ_TTL:
            kept = (now, self._read_recent(dataset, span, fields))
            self._long_reads[key] = kept
        return kept[1]

    def _read_recent(self, dataset: str, span: timedelta, fields: tuple[str, ...]) -> pd.DataFrame:
        """Reads the documents of a span ending with the newest document from the repository."""
        latest = self.latest(dataset, fields)
        if latest is None:
            return pd.DataFrame()
        newest, _ = latest
        # The stop is exclusive, so the newest document needs a stop just after it; MongoDB keeps whole milliseconds
        return self._timeseries.read_range(collection_of(dataset), newest - span, newest + timedelta(milliseconds=1), fields)

    def metadata(self, dataset: str) -> Metadata:
        """Returns the metadata record of a dataset as JSON-compatible dictionary; empty if none is stored."""
        record = self._metadata.get(dataset)
        return record.model_dump(mode="json") if record else {}

    def feed_summaries(self, since: datetime) -> list[FeedJobSummary]:
        """Returns the job summary of every feed with a job, counting the outcomes since a time."""
        return self._jobs.feed_summaries(since)
