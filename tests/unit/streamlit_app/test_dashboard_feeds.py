from datetime import UTC, datetime, timedelta

import pytest

from space_weather_analysis.jobs.models import ChunkGap, FeedJobSummary, Job, JobStatus
from streamlit_app.dashboard.panels.feeds import feed_status_text

NOW = datetime(2024, 1, 1, 12, tzinfo=UTC)
DAY = timedelta(days=1)
MINUTE = timedelta(minutes=1)


def _summary(status: JobStatus, created_ago: timedelta, reasons: tuple[str, ...] = (), error: str | None = None) -> FeedJobSummary:
    """Builds the summary of the feed `live` whose newest job has the given status, age, gap reasons and error."""
    gaps = [ChunkGap(dataset_id="set", start=NOW - DAY, stop=NOW, reason=reason) for reason in reasons]
    latest = Job(id="0" * 24, status=status, created_at=NOW - created_ago, missing_chunks=gaps, error=error)
    return FeedJobSummary(feed="live", latest=latest, runs=10, completed=7, with_gaps=2, failed=1)


COUNTS = "Last 24 h: 10 runs, 7 complete, 2 with gaps, 1 failed"


@pytest.mark.parametrize(
    ("summary", "interval", "expected"),
    [
        pytest.param(_summary(JobStatus.COMPLETED, MINUTE), MINUTE, f"**live**: :green[ok], last run 1 min ago, every 1 min  \n{COUNTS}", id="ok"),
        pytest.param(
            _summary(JobStatus.COMPLETED, MINUTE, ("SourceUnreachableError: no answer", "Not requested, the provider was unreachable: x")),
            MINUTE,
            f"**live**: :orange[gaps], last run 1 min ago, every 1 min  \n{COUNTS}  \nMissing chunks: 2 source unreachable",
            id="gaps-with-readable-cause",
        ),
        pytest.param(
            _summary(JobStatus.FAILED, MINUTE, error="ValueError: unknown dataset"),
            MINUTE,
            f"**live**: :red[failed], last run 1 min ago, every 1 min  \n{COUNTS}  \nFailed: ValueError: unknown dataset",
            id="failed-with-error",
        ),
        pytest.param(
            _summary(JobStatus.COMPLETED, 10 * MINUTE),
            MINUTE,
            f"**live**: :orange[overdue], last run 10 min ago, every 1 min  \n{COUNTS}",
            id="overdue",
        ),
        pytest.param(
            _summary(JobStatus.RUNNING, MINUTE),
            None,
            f"**live**: :blue[running], last run 1 min ago, on start only  \n{COUNTS}",
            id="running-start-only",
        ),
    ],
)
def test_feed_status_text_lists_state_schedule_counts_and_causes(summary: FeedJobSummary, interval: timedelta | None, expected: str) -> None:
    """The status names state, age and schedule, counts the outcomes and gives readable causes of gaps or failure."""
    assert feed_status_text(summary, interval, DAY, NOW) == expected
