from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from streamlit_app import data
from streamlit_app.figures.goes import proton_figure, xray_figure
from streamlit_app.figures.kp import kp_figure
from streamlit_app.figures.solar_activity import cme_table_frame, sunspot_figure
from streamlit_app.figures.tiles import Tile, kp_tile, proton_tile, sunspot_tile, xray_tile


@dataclass(frozen=True)
class Feed:
    """A stored dataset the dashboard shows.

    Attributes:
        collection: The collection holding the data documents.
        fields: The data fields the dashboard reads.
    """

    collection: str
    fields: tuple[str, ...]


KP = Feed("Kp", ("Kp",))
XRAY = Feed("goesp_xray_flux_P1M", ("Long_Wave", "Short_Wave"))
PROTONS = Feed("goesp_part_flux_P5M", ("P10",))
SUNSPOTS_DAILY = Feed("silso_sunspot_daily", ("sunspot_number",))
SUNSPOTS_MONTHLY = Feed("silso_sunspot_monthly", ("sunspot_number",))
SUNSPOTS_SMOOTHED = Feed("silso_sunspot_smoothed", ("sunspot_number",))
CMES = Feed("cme_catalog", ("linear_speed", "width", "central_pa", "is_halo", "kinetic_energy", "remarks"))

# Time span of each chart, ending with the newest stored document of its feed
KP_SPAN = timedelta(days=7)
GOES_SPAN = timedelta(days=3)
# About three 11-year solar cycles
SUNSPOT_SPAN = timedelta(days=365 * 35)
CME_SPAN = timedelta(days=90)

# Fixed chart height in pixels, so that the whole grid fits on one screen
CHART_HEIGHT = 300
CME_TABLE_ROWS = 20


def _read_recent(feed: Feed, span: timedelta) -> pd.DataFrame:
    """Reads the span of a feed that ends with its newest document."""
    return data.read_recent(feed.collection, span, feed.fields)


def _show_tile(tile: Tile) -> None:
    """Shows a key figure tile with its value, NOAA level badge and time stamp."""
    with st.container(border=True):
        st.metric(tile.title, tile.value)
        if tile.level is not None:
            st.html(f'<span style="background:{tile.color};color:#000;border-radius:4px;padding:1px 8px;font-weight:600">{tile.level}</span>')
        st.caption(f"{tile.time:%Y-%m-%d %H:%M} UTC" if tile.time is not None else "No data stored")


def _show_chart(fig: go.Figure) -> None:
    """Shows a figure at the fixed dashboard height with small margins."""
    fig.update_layout(height=CHART_HEIGHT, margin={"l": 10, "r": 10, "t": 40, "b": 10}, title_font_size=14, xaxis_title=None)
    st.plotly_chart(fig, width="stretch")


def _show_missing(feed: Feed) -> None:
    """Shows a note in place of a chart whose feed has no documents."""
    with st.container(border=True, height=CHART_HEIGHT):
        st.info(f"No data stored in {feed.collection}.")


def _show_feed_chart(feed: Feed, span: timedelta, build_figure: Callable[[pd.DataFrame], go.Figure]) -> None:
    """Shows the chart of a feed over its span, or a note if the feed has no documents."""
    frame = _read_recent(feed, span)
    if frame.empty:
        _show_missing(feed)
    else:
        _show_chart(build_figure(frame))


def _show_sunspots() -> None:
    """Shows the monthly mean and, if stored, the 13-month smoothed sunspot numbers."""
    monthly = _read_recent(SUNSPOTS_MONTHLY, SUNSPOT_SPAN)
    if monthly.empty:
        _show_missing(SUNSPOTS_MONTHLY)
        return
    smoothed = _read_recent(SUNSPOTS_SMOOTHED, SUNSPOT_SPAN)
    _show_chart(sunspot_figure(monthly, None if smoothed.empty else smoothed))


def _show_cmes() -> None:
    """Shows the most recent CMEs (coronal mass ejections) as a table of the chart height."""
    events = _read_recent(CMES, CME_SPAN)
    if events.empty:
        _show_missing(CMES)
        return
    with st.container(border=True, height=CHART_HEIGHT):
        st.markdown("**Recent CMEs**")
        st.dataframe(cme_table_frame(events, count=CME_TABLE_ROWS), hide_index=True)


st.title("Space Weather Dashboard")

tiles = [
    kp_tile(data.read_latest(KP.collection, KP.fields)),
    xray_tile(data.read_latest(XRAY.collection, XRAY.fields)),
    proton_tile(data.read_latest(PROTONS.collection, PROTONS.fields)),
    sunspot_tile(data.read_latest(SUNSPOTS_DAILY.collection, SUNSPOTS_DAILY.fields)),
]
for column, tile in zip(st.columns(len(tiles)), tiles, strict=True):
    with column:
        _show_tile(tile)

kp_column, xray_column, proton_column = st.columns(3)
with kp_column:
    _show_feed_chart(KP, KP_SPAN, kp_figure)
with xray_column:
    _show_feed_chart(XRAY, GOES_SPAN, xray_figure)
with proton_column:
    _show_feed_chart(PROTONS, GOES_SPAN, proton_figure)

sunspot_column, cme_column = st.columns(2)
with sunspot_column:
    _show_sunspots()
with cme_column:
    _show_cmes()
