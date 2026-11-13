from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel

from space_weather_analysis.dashboard.freshness import DatasetFreshness, dataset_freshness
from space_weather_analysis.dashboard.labels import dataset_title
from space_weather_analysis.dashboard.panels.base import PAYLOAD, PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.jobs.models import FeedJobSummary, JobStatus
from space_weather_analysis.util.time_utils import IsoDuration, SecondsDuration

# Multiple of the interval after which a feed without newer job counts as overdue
OVERDUE_FACTOR = 3
# Readable causes of missing chunks and failures, by the start of the reason the pipelines record
_CAUSES = {
    "SourceUnreachableError": "provider did not answer",
    "Not requested, the provider was unreachable": "provider did not answer",
    "ResourceNotFoundError": "data not available at the provider",
    "DataRetrievalError": "provider refused the request",
    "DataProcessingError": "provider data in an unexpected form",
}
# Cause of any other reason, a fault of this site whose details stay in the logs instead of the dashboard
UNKNOWN_CAUSE = "processing error on this site"


class FeedsPanel(PanelConfig):
    """A readable status of every fetched dataset, built from the jobs of its feed instead of the logs.

    Attributes:
        window: The span the jobs are read in, ending now.
    """

    window: IsoDuration = timedelta(days=1)


class FeedState(StrEnum):
    """The state of a feed, or of one of its datasets, by its newest job."""

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
    """Turns a recorded reason into a readable cause; UNKNOWN_CAUSE for a reason of no known kind."""
    for start, cause in _CAUSES.items():
        if reason.startswith(start):
            return cause
    return UNKNOWN_CAUSE


def _causes_text(reasons: Iterable[str]) -> str:
    """Counts the readable causes of reasons, the most frequent first."""
    counts = Counter(_cause(reason) for reason in reasons)
    return ", ".join(f"{count} {cause}" for cause, count in counts.most_common())


def dataset_state(summary: FeedJobSummary, state: FeedState, dataset: str) -> FeedState:
    """Rates one dataset of a feed: a feed with missing chunks counts as gaps only for the datasets that miss chunks.

    Args:
        summary: The job summary of the feed.
        state: The state of the feed.
        dataset: The dataset ID.

    Returns:
        The state of the dataset.
    """
    if state != FeedState.GAPS:
        return state
    return FeedState.GAPS if any(gap.dataset_id == dataset for gap in summary.latest.missing_chunks) else FeedState.OK


def dataset_problem(summary: FeedJobSummary, dataset: str) -> str:
    """Names what went wrong for one dataset in the newest job of its feed: the causes of its missing chunks or the error of a broken job.

    Args:
        summary: The job summary of the feed.
        dataset: The dataset ID.

    Returns:
        A line starting with "Failed:" or "Missing parts:"; empty if nothing went wrong for the dataset.
    """
    latest = summary.latest
    reasons = [gap.reason for gap in latest.missing_chunks if gap.dataset_id == dataset]
    if latest.status == JobStatus.FAILED:
        # Without missing chunks the job broke; its error text is named by its kind only
        return f"Failed: {_causes_text(reasons)}" if reasons else f"Failed: {_cause(latest.error or '')}"
    return f"Missing parts: {_causes_text(reasons)}" if reasons else ""


class DatasetStatus(BaseModel):
    """The status of one dataset in the data updates payload.

    Attributes:
        dataset: The dataset ID.
        title: The title of the dataset; the dataset ID for a dataset without title.
        state: The state of the dataset in the newest fetch of its feed.
        problem: What went wrong for the dataset in the newest fetch; empty if nothing.
        last_run: When the newest fetch was started.
        interval: The time between two fetches; None for data fetched once at start.
        freshness: How current the stored data are; None without stored data.
    """

    model_config = PAYLOAD

    dataset: str
    title: str
    state: FeedState
    problem: str
    last_run: datetime
    interval: SecondsDuration | None
    freshness: DatasetFreshness | None


class FeedsPayload(PanelPayload):
    """The JSON document of the data updates panel.

    Attributes:
        title: The heading.
        overdue_factor: The number of intervals without a new fetch after which data count as overdue.
        datasets: The status of every dataset of a feed with jobs, in the order of the feeds and their datasets.
    """

    title: str
    overdue_factor: int = OVERDUE_FACTOR
    datasets: list[DatasetStatus]


def build_feeds(panel: FeedsPanel, reader: DashboardReader, context: PanelContext) -> FeedsPayload:
    """Builds the JSON document of the data updates panel: one line per dataset, rated by the newest fetch of its feed.

    Args:
        panel: The panel settings.
        reader: The source of the job summaries, the newest documents and the metadata.
        context: The context of the export, with its time and the interval and datasets of each feed.

    Returns:
        The payload.
    """
    statuses: dict[str, DatasetStatus] = {}
    for summary in reader.feed_summaries(context.now - panel.window):
        interval = context.feed_schedule.get(summary.feed)
        state = feed_state(summary, interval, context.now)
        for dataset in context.feed_datasets.get(summary.feed, ()):
            # A dataset of several feeds is rated by the first feed listing it
            if dataset in statuses:
                continue
            metadata = reader.metadata(dataset)
            latest = reader.latest(dataset, ())
            freshness = None if latest is None else dataset_freshness(dataset, latest[0], metadata, context.feed_intervals.get(dataset))
            statuses[dataset] = DatasetStatus(
                dataset=dataset,
                title=dataset_title(metadata) or dataset,
                state=dataset_state(summary, state, dataset),
                problem=dataset_problem(summary, dataset),
                last_run=summary.latest.created_at,
                interval=interval,
                freshness=freshness,
            )
    return FeedsPayload(type=panel.type, title=panel.title or "Data updates", datasets=list(statuses.values()))
