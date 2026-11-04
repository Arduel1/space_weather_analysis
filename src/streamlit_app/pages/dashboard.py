from datetime import UTC, datetime, timedelta

import streamlit as st

from streamlit_app import data
from streamlit_app.dashboard.config import CONFIG_ERRORS, DashboardConfig, DashboardSettings, load_dashboard_config
from streamlit_app.dashboard.panel_types import PANEL_TYPES

# Time between two checks for newly stored documents; the page is drawn again only when one arrived
NEW_DATA_CHECK_INTERVAL = timedelta(seconds=5)
# Session state key of the newest time stamp of each shown collection at the last drawing
NEWEST_TIMES_KEY = "dashboard_newest_times"
# Session state key of the live updates toggle
LIVE_KEY = "dashboard_live"


def _show_panels(config: DashboardConfig, now: datetime) -> None:
    """Draws the panels row by row, each row split by the relative widths of its panels."""
    for row_number, row in enumerate(config.rows()):
        for column_number, (column, panel) in enumerate(zip(st.columns([panel.width for panel in row]), row, strict=True)):
            with column:
                PANEL_TYPES[panel.type].render(panel, now, f"panel-{row_number}-{column_number}")


@st.fragment(run_every=NEW_DATA_CHECK_INTERVAL)
def _redraw_on_new_data(collections: frozenset[str]) -> None:
    """Draws the page again when a shown collection holds a newer document than at the last drawing, unless live updates are paused.

    Args:
        collections: The collections the page shows.
    """
    if not st.session_state.get(LIVE_KEY, True):
        return
    newest = {collection: data.read_newest_time(collection) for collection in sorted(collections)}
    previous = st.session_state.get(NEWEST_TIMES_KEY)
    st.session_state[NEWEST_TIMES_KEY] = newest
    if previous is not None and previous != newest:
        data.clear_read_cache()
        st.rerun(scope="app")


title_column, live_column = st.columns([5, 1], vertical_alignment="bottom")
with title_column:
    st.title("Space Weather Dashboard")
with live_column:
    st.toggle("Live updates", value=True, key=LIVE_KEY, help="Pause to zoom and pan into the loaded history; every update redraws the charts.")

try:
    dashboard_config = load_dashboard_config(DashboardSettings().feeds_file)
except CONFIG_ERRORS as error:
    st.error(f"The dashboard table of the feed file cannot be read: {error}")
    st.stop()

_show_panels(dashboard_config, datetime.now(UTC))
_redraw_on_new_data(frozenset(dashboard_config.collections()))
