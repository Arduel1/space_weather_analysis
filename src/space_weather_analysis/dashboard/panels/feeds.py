from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel

from space_weather_analysis.dashboard.panels.base import PAYLOAD, PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.jobs.models import FeedJobSummary, JobStatus
from space_weather_analysis.util.time_utils import IsoDuration, SecondsDuration

# Multiple of the interval after which a feed without newer job counts as overdue
OVERDUE_FACTOR = 3
# Readable causes of missing chunks and failures, by the start of the reason the pipelines record
_CAUSES = {
    "SourceUnreachableError": "source unreachable",
    "Not requested, the provider was unreachable": "source unreachable",
    "DataRetrievalError": "request failed",
    "ResourceNotFoundError": "not found at the source",
    "DataProcessingError": "unreadable data",
}


class FeedsPanel(PanelConfig):
    """A readable status of every configured feed, built from its jobs instead of the logs.

    Attributes:
        window: The span the outcomes of the jobs are counted in, ending now.
    """

    window: IsoDuration = timedelta(days=1)


class FeedState(StrEnum):
    """The state of a feed by its newest job."""

    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    # Completed, but no newer job for OVERDUE_FACTOR intervals
    OVERDUE = "overdue"
    # Completed with missing chunks
    GAPS = "gaps"
    OK = "ok"


def feed_state(summary: FeedJobSummary, interval: timedelta | None, now: datetime) -> FeedState:
    """Rates a feed by the status and age of its newest job.

    Args:
        summary: The job summary of the feed.
        interval: The interval of the feed; None for a feed queued only on start.
        now: The current time.

    Returns:
        The state.
    """
    latest = summary.latest
    if latest.status == JobStatus.PENDING:
        return FeedState.PENDING
    if latest.status == JobStatus.RUNNING:
        return FeedState.RUNNING
    if latest.status == JobStatus.FAILED:
        return FeedState.FAILED
    if interval is not None and now - latest.created_at > interval * OVERDUE_FACTOR:
        return FeedState.OVERDUE
    return FeedState.GAPS if latest.missing_chunks else FeedState.OK


def _cause(reason: str) -> str:
    """Turns a recorded reason into a readable cause, the reason itself if no cause is known for it."""
    for start, cause in _CAUSES.items():
        if reason.startswith(start):
            return cause
    return reason.split(":")[0]


def _causes_text(reasons: Iterable[str]) -> str:
    """Counts the readable causes of reasons, the most frequent first."""
    counts = Counter(_cause(reason) for reason in reasons)
    return ", ".join(f"{count} {cause}" for cause, count in counts.most_common())


def feed_problem(summary: FeedJobSummary) -> str:
    """Names what went wrong in the newest job of a feed: the causes of failure or missing chunks, or the error of a broken job.

    Args:
        summary: The job summary of the feed.

    Returns:
        A line starting with "Failed:" or "Missing chunks:"; empty if nothing went wrong.
    """
    latest = summary.latest
    if latest.status == JobStatus.FAILED and latest.missing_chunks:
        return f"Failed: {_causes_text(gap.reason for gap in latest.missing_chunks)}"
    if latest.status == JobStatus.FAILED and latest.error:
        # Without missing chunks the job was rejected or broke, and its error text says why in words
        return f"Failed: {latest.error}"
    if latest.missing_chunks:
        return f"Missing chunks: {_causes_text(gap.reason for gap in latest.missing_chunks)}"
    return ""


class FeedStatus(BaseModel):
    """The status of one feed in a feeds payload.

    Attributes:
        feed: The name of the feed.
        state: The state by its newest job.
        last_run: When the newest job was created.
        interval: The interval of the feed; None for a feed queued only on start.
        runs: The jobs created within the window.
        completed: The jobs of the window that ended without missing chunks.
        with_gaps: The jobs of the window that ended with missing chunks.
        failed: The jobs of the window that failed.
        problem: What went wrong in the newest job; empty if nothing.
    """

    model_config = PAYLOAD

    feed: str
    state: FeedState
    last_run: datetime
    interval: SecondsDuration | None
    runs: int
    completed: int
    with_gaps: int
    failed: int
    problem: str


class FeedsPayload(PanelPayload):
    """The JSON document of the feed status panel.

    Attributes:
        title: The heading.
        window: The span the outcomes are counted in, ending at the export.
        feeds: The status of every feed with jobs, sorted by feed name.
    """

    title: str
    window: SecondsDuration
    feeds: list[FeedStatus]


def build_feeds(panel: FeedsPanel, reader: DashboardReader, context: PanelContext) -> FeedsPayload:
    """Builds the JSON document of the feed status panel from the jobs of the feeds.

    Args:
        panel: The panel settings.
        reader: The source of the job summaries.
        context: The context of the export, with its time and the interval of each feed.

    Returns:
        The payload.
    """
    feeds = []
    for summary in reader.feed_summaries(context.now - panel.window):
        interval = context.feed_schedule.get(summary.feed)
        feeds.append(
            FeedStatus(
                feed=summary.feed,
                state=feed_state(summary, interval, context.now),
                last_run=summary.latest.created_at,
                interval=interval,
                runs=summary.runs,
                completed=summary.completed,
                with_gaps=summary.with_gaps,
                failed=summary.failed,
                problem=feed_problem(summary),
            )
        )
    return FeedsPayload(type=panel.type, title=panel.title or "Feeds", window=panel.window, feeds=feeds)
