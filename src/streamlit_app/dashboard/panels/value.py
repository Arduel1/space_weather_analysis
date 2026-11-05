import streamlit as st

from space_weather_analysis.dashboard.panels.base import PanelContext, collection_of
from space_weather_analysis.dashboard.panels.value import ValuePanel, value_view
from streamlit_app import data
from streamlit_app.dashboard.drawing import HELP_LINE_BREAK, panel_freshness


def render_value(panel: ValuePanel, context: PanelContext, key: str) -> None:
    """Shows the tile with its value, level badge, time stamp with freshness, and source.

    Args:
        panel: The panel settings.
        context: The context of the drawing.
        key: The key of the panel elements, unique on the page.
    """
    del key
    metadata = data.get_metadata(panel.dataset)
    view = value_view(panel, data.read_latest(collection_of(panel.dataset), (panel.field,)), metadata)
    with st.container(border=True):
        st.metric(view.title, view.text, help=HELP_LINE_BREAK.join(view.help_lines) or None)
        if view.level is not None:
            st.html(
                f'<span style="background:{view.level.color};color:#000;border-radius:4px;padding:1px 8px;font-weight:600">{view.level.label}</span>'
            )
        if view.time is None:
            st.caption("No data stored")
        else:
            state = panel_freshness({panel.dataset: view.time}, {panel.dataset: metadata}, context.feed_intervals, context.now)
            st.caption(f"{view.time:%Y-%m-%d %H:%M} UTC, {state}")
        st.caption(view.source)
