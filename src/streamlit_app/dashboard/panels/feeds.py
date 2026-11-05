from datetime import datetime, timedelta

import streamlit as st

from space_weather_analysis.dashboard.freshness import age_text
from space_weather_analysis.dashboard.panels.base import PanelContext
from space_weather_analysis.dashboard.panels.feeds import FeedsPanel, FeedState, feed_problem, feed_state
from space_weather_analysis.jobs.models import FeedJobSummary
from streamlit_app import data
from streamlit_app.dashboard.drawing import HELP_LINE_BREAK, PANEL_HEIGHT

# Streamlit markdown color of each feed state
_COLORS = {
    FeedState.PENDING: "blue",
    FeedState.RUNNING: "blue",
    FeedState.FAILED: "red",
    FeedState.OVERDUE: "orange",
    FeedState.GAPS: "orange",
    FeedState.OK: "green",
}


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
    state = feed_state(summary, interval, now)
    schedule = f"every {age_text(interval)}" if interval is not None else "on start only"
    lines = [
        f"**{summary.feed}**: :{_COLORS[state]}[{state}], last run {age_text(now - latest.created_at)} ago, {schedule}",
        f"Last {age_text(window)}: {summary.runs} runs, {summary.completed} complete, {summary.with_gaps} with gaps, {summary.failed} failed",
    ]
    problem = feed_problem(summary)
    if problem:
        lines.append(problem)
    return HELP_LINE_BREAK.join(lines)


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
