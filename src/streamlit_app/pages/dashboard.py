from datetime import UTC, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from streamlit_app import data
from streamlit_app.dashboard.config import CONFIG_ERRORS, DashboardConfig, DashboardSettings, FieldRef, load_dashboard_config
from streamlit_app.figures.goes import proton_figure, xray_figure
from streamlit_app.figures.kp import kp_figure
from streamlit_app.figures.solar_activity import CME_DISPLAY_COLUMNS, cme_table_frame, sunspot_figure
from streamlit_app.figures.tiles import LatestDocument, Tile, kp_tile, proton_tile, sunspot_tile, xray_tile

# Time between two checks for newly stored documents; the page is drawn again only when one arrived
NEW_DATA_CHECK_INTERVAL = timedelta(seconds=5)
# Session state key of the newest time stamp of each shown collection at the last drawing
NEWEST_TIMES_KEY = "dashboard_newest_times"
# Session state key of the live updates toggle
LIVE_KEY = "dashboard_live"

# Fixed chart height in pixels, so that the whole grid fits on one screen
CHART_HEIGHT = 300


def _latest(ref: FieldRef | None) -> LatestDocument | None:
    """Reads the newest document of a configured field; None without configuration or document."""
    return data.read_latest(ref.collection, (ref.field,)) if ref is not None else None


def _show_tile(tile: Tile) -> None:
    """Shows a key figure tile with its value, NOAA level badge, time stamp and note."""
    with st.container(border=True):
        st.metric(tile.title, tile.value)
        if tile.level is not None:
            st.html(f'<span style="background:{tile.color};color:#000;border-radius:4px;padding:1px 8px;font-weight:600">{tile.level}</span>')
        st.caption(f"{tile.time:%Y-%m-%d %H:%M} UTC" if tile.time is not None else "No data stored")
        if tile.note is not None:
            st.caption(tile.note)


def _show_chart(fig: go.Figure, view: timedelta, now: datetime, key: str) -> None:
    """Shows a figure at the fixed dashboard height over the view ending now.

    Every drawing replaces the figure and with it a zoom of the viewer, so zooming and panning into the loaded history
    is meant for paused live updates.
    """
    fig.update_layout(height=CHART_HEIGHT, margin={"l": 10, "r": 10, "t": 40, "b": 10}, title_font_size=14, xaxis_title=None)
    fig.update_xaxes(range=[now - view, now])
    st.plotly_chart(fig, width="stretch", key=key)


def _show_missing(collection: str) -> None:
    """Shows a note in place of a chart whose collection has no documents."""
    with st.container(border=True, height=CHART_HEIGHT):
        st.info(f"No data stored in {collection}.")


def _show_tiles(config: DashboardConfig) -> None:
    """Shows the configured key figure tiles side by side."""
    tiles_config = config.tiles
    tiles = []
    if tiles_config.kp is not None:
        estimate = tiles_config.kp.estimate
        tiles.append(
            kp_tile(
                _latest(tiles_config.kp),
                kp_column=tiles_config.kp.field,
                estimate=_latest(estimate),
                estimate_column=estimate.field if estimate is not None else "estimated_kp",
            )
        )
    if tiles_config.xray is not None:
        tiles.append(xray_tile(_latest(tiles_config.xray), long_column=tiles_config.xray.field))
    if tiles_config.protons is not None:
        tiles.append(proton_tile(_latest(tiles_config.protons), proton_column=tiles_config.protons.field))
    if tiles_config.sunspots is not None:
        tiles.append(sunspot_tile(_latest(tiles_config.sunspots), sunspot_column=tiles_config.sunspots.field))
    if not tiles:
        return
    for column, tile in zip(st.columns(len(tiles)), tiles, strict=True):
        with column:
            _show_tile(tile)


def _show_kp_chart(config: DashboardConfig, now: datetime) -> None:
    """Shows the official Kp as bars and, if configured, the estimated Kp as line."""
    chart = config.charts.kp
    if chart is None:
        return
    official = data.read_recent(chart.collection, chart.history, (chart.field,))
    estimate = None
    if chart.estimate is not None:
        estimate_frame = data.read_recent(chart.estimate.collection, chart.history, (chart.estimate.field,))
        estimate = estimate_frame[chart.estimate.field] if not estimate_frame.empty else None
    if official.empty and estimate is None:
        _show_missing(chart.collection)
        return
    frame = official if not official.empty else pd.DataFrame({chart.field: pd.Series(dtype=float)}, index=pd.DatetimeIndex([], tz=UTC))
    _show_chart(kp_figure(frame, kp_column=chart.field, estimate=estimate), chart.view, now, "kp")


def _show_xray_chart(config: DashboardConfig, now: datetime) -> None:
    """Shows both X-ray channels."""
    chart = config.charts.xray
    if chart is None:
        return
    frame = data.read_recent(chart.collection, chart.history, (chart.long_field, chart.short_field))
    if frame.empty:
        _show_missing(chart.collection)
        return
    _show_chart(xray_figure(frame, long_column=chart.long_field, short_column=chart.short_field), chart.view, now, "xray")


def _show_proton_chart(config: DashboardConfig, now: datetime) -> None:
    """Shows the >=10 MeV proton flux."""
    chart = config.charts.protons
    if chart is None:
        return
    frame = data.read_recent(chart.collection, chart.history, (chart.field,))
    if frame.empty:
        _show_missing(chart.collection)
        return
    _show_chart(proton_figure(frame, proton_column=chart.field), chart.view, now, "protons")


def _show_sunspot_chart(config: DashboardConfig, now: datetime) -> None:
    """Shows the monthly mean and, if configured and stored, the smoothed sunspot numbers."""
    chart = config.charts.sunspots
    if chart is None:
        return
    monthly = data.read_recent(chart.monthly.collection, chart.history, (chart.monthly.field,))
    if monthly.empty:
        _show_missing(chart.monthly.collection)
        return
    smoothed = None
    if chart.smoothed is not None:
        smoothed_frame = data.read_recent(chart.smoothed.collection, chart.history, (chart.smoothed.field,))
        # The figure reads both frames from the column of the monthly numbers
        smoothed = smoothed_frame.rename(columns={chart.smoothed.field: chart.monthly.field}) if not smoothed_frame.empty else None
    _show_chart(sunspot_figure(monthly, smoothed, sunspot_column=chart.monthly.field), chart.view, now, "sunspots")


def _show_cme_table(config: DashboardConfig) -> None:
    """Shows the most recent CMEs (coronal mass ejections) as a table of the chart height."""
    table = config.charts.cmes
    if table is None:
        return
    events = data.read_recent(table.collection, table.span, tuple(CME_DISPLAY_COLUMNS))
    if events.empty:
        _show_missing(table.collection)
        return
    with st.container(border=True, height=CHART_HEIGHT):
        st.markdown("**Recent CMEs**")
        st.dataframe(cme_table_frame(events, count=table.count), hide_index=True)


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

drawn_at = datetime.now(UTC)
_show_tiles(dashboard_config)

kp_column, xray_column, proton_column = st.columns(3)
with kp_column:
    _show_kp_chart(dashboard_config, drawn_at)
with xray_column:
    _show_xray_chart(dashboard_config, drawn_at)
with proton_column:
    _show_proton_chart(dashboard_config, drawn_at)

sunspot_column, cme_column = st.columns(2)
with sunspot_column:
    _show_sunspot_chart(dashboard_config, drawn_at)
with cme_column:
    _show_cme_table(dashboard_config)

_redraw_on_new_data(frozenset(dashboard_config.collections()))
