from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta

import streamlit as st

from space_weather_analysis.jobs.models import FeedJobSummary, JobStatus
from space_weather_analysis.util.time_utils import IsoDuration
from streamlit_app import data
from streamlit_app.dashboard.freshness import age_text
from streamlit_app.dashboard.labels import HELP_LINE_BREAK
from streamlit_app.dashboard.panels.base import PANEL_HEIGHT, PanelConfig, PanelContext

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


def _cause(reason: str) -> str:
    """Turns a recorded reason into a readable cause, the reason itself if no cause is known for it."""
    for start, cause in _CAUSES.items():
        if reason.startswith(start):
            return cause
    return reason.split(":")[0]


def feed_status_text(summary: FeedJobSummary, interval: timedelta | None, window: timedelta, now: datetime) -> str:
    """Writes the status of one feed as listed lines of markdown.

    The first line names the feed, its state, the age of its newest job and its interval; the second counts the
    outcomes within the window; a third names the causes of failure or missing chunks of the newest job.

    Args:
        summary: The job summary of the feed.
        interval: The interval of the feed; None for a feed queued only on start.
        window: The span the outcomes are counted in.
        now: The current time.

    Returns:
        The markdown text.
    """
    latest = summary.latest
    if latest.status in {JobStatus.PENDING, JobStatus.RUNNING}:
        state = f":blue[{latest.status.lower()}]"
    elif latest.status == JobStatus.FAILED:
        state = ":red[failed]"
    elif interval is not None and now - latest.created_at > interval * OVERDUE_FACTOR:
        state = ":orange[overdue]"
    else:
        state = ":orange[gaps]" if latest.missing_chunks else ":green[ok]"
    schedule = f"every {age_text(interval)}" if interval is not None else "on start only"
    lines = [
        f"**{summary.feed}**: {state}, last run {age_text(now - latest.created_at)} ago, {schedule}",
        f"Last {age_text(window)}: {summary.runs} runs, {summary.completed} complete, {summary.with_gaps} with gaps, {summary.failed} failed",
    ]
    if latest.status == JobStatus.FAILED and latest.missing_chunks:
        lines.append(f"Failed: {_causes_text(gap.reason for gap in latest.missing_chunks)}")
    elif latest.status == JobStatus.FAILED and latest.error:
        # Without missing chunks the job was rejected or broke, and its error text says why in words
        lines.append(f"Failed: {latest.error}")
    elif latest.missing_chunks:
        lines.append(f"Missing chunks: {_causes_text(gap.reason for gap in latest.missing_chunks)}")
    return HELP_LINE_BREAK.join(lines)


def _causes_text(reasons: Iterable[str]) -> str:
    """Counts the readable causes of reasons, the most frequent first."""
    counts = Counter(_cause(reason) for reason in reasons)
    return ", ".join(f"{count} {cause}" for cause, count in counts.most_common())


def render_feeds(panel: FeedsPanel, context: PanelContext, key: str) -> None:
    """Shows the status of every feed with jobs, one block per feed, at the panel height.

    Args:
        panel: The panel settings.
        context: The context of the drawing, with the interval of each feed.
        key: The key of the panel elements, unique on the page.
    """
    del key
    summaries = data.get_feed_summaries(context.now - panel.window)
    with st.container(border=True, height=PANEL_HEIGHT):
        st.markdown(f"**{panel.title or 'Feeds'}**")
        if not summaries:
            st.info("No feed has run yet.")
        for summary in summaries:
            st.markdown(feed_status_text(summary, context.feed_schedule.get(summary.feed), panel.window, context.now))
