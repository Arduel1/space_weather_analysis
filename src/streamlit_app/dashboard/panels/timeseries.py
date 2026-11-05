import math
from collections.abc import Mapping
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from space_weather_analysis.dashboard.labels import Metadata, dataset_title, field_label, field_unit, sources_line
from space_weather_analysis.dashboard.panels.base import PanelContext, collection_of
from space_weather_analysis.dashboard.panels.timeseries import TimeseriesPanel, break_gaps, series_values
from space_weather_analysis.dashboard.validity import describe_validity, valid_mask, valid_range
from space_weather_analysis.util.time_utils import parse_iso_duration
from streamlit_app import data
from streamlit_app.dashboard.drawing import HELP_LINE_BREAK, PANEL_HEIGHT, panel_freshness

# Share of the spacing of two values a bar fills, so that neighbouring bars stay apart
_BAR_FILL = 0.9
_LEGEND_ON_TOP = {"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1}


def _bar_width_ms(index: pd.Index) -> float | None:
    """Returns the width of bars in milliseconds from the typical spacing of the times; None with fewer than two times."""
    if len(index) < 2:
        return None
    # Datetime64 values in nanoseconds, so their differences are integer nanoseconds
    nanoseconds = pd.DatetimeIndex(index).to_numpy(dtype="datetime64[ns]").astype(np.int64)
    return float(np.median(np.diff(nanoseconds))) / 1e6 * _BAR_FILL


def timeseries_figure(
    panel: TimeseriesPanel, frames: Mapping[str, pd.DataFrame], metadata: Mapping[str, Metadata], now: datetime, *, hide_invalid: bool = False
) -> go.Figure:
    """Builds the chart of a time series panel over its view ending now.

    Configured title, labels and axis title take precedence over the metadata, which takes precedence over field names.

    Args:
        panel: The panel settings.
        frames: The loaded documents of each dataset of the panel, by dataset ID, with a UTC DatetimeIndex.
        metadata: The metadata record of each dataset of the panel, by dataset ID; missing or empty if it has none.
        now: The end of the view.
        hide_invalid: Whether fill values and values outside the valid range of each series are left out.

    Returns:
        One trace per series with its unit in the hover text, a line at each level of the scale, and the time axis over the view.
    """
    log_axis = panel.y_axis == "log"
    fig = go.Figure()
    for series in panel.series:
        frame = frames.get(series.dataset, pd.DataFrame())
        record = metadata.get(series.dataset, {})
        values = series_values(frame, series, log_axis)
        if hide_invalid:
            values = values.where(valid_mask(values, valid_range(record, series.field, series.valid_min, series.valid_max)))
        if series.style == "line":
            values = break_gaps(values, parse_iso_duration(record.get("time", {}).get("cadence") or ""))
        name = field_label(record, series.field, series.label)
        hover = f"%{{y:.3g}} {field_unit(record, series.field)}".rstrip()
        line = {"color": series.color} if series.color else {}
        if series.style == "bars":
            fig.add_trace(go.Bar(x=values.index, y=values, width=_bar_width_ms(values.index), name=name, marker=line, hovertemplate=hover))
        else:
            mode = "lines" if series.style == "line" else "markers"
            fig.add_trace(go.Scattergl(x=values.index, y=values, mode=mode, name=name, line={**line, "width": 1.5}, marker=line, hovertemplate=hover))

    scale = panel.resolved_scale()
    for level in scale.levels if scale is not None else ():
        fig.add_hline(
            y=level.value,
            line_dash="dot",
            line_width=1,
            line_color=level.color,
            annotation_text=level.label,
            annotation_position="top right",
            annotation_font_size=10,
        )

    first_series = panel.series[0]
    first_record = metadata.get(first_series.dataset, {})
    y_axis: dict[str, object] = {"type": panel.y_axis, "title": panel.y_title or field_unit(first_record, first_series.field) or None}
    if panel.y_range is not None:
        low, high = panel.y_range
        # Plotly takes the range of a log axis as exponents of ten
        y_axis["range"] = [math.log10(low), math.log10(high)] if log_axis else [low, high]
    fig.update_layout(
        title=dataset_title(first_record, panel.title),
        height=PANEL_HEIGHT,
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        title_font_size=14,
        hovermode="x unified",
        showlegend=len(panel.series) > 1,
        legend=_LEGEND_ON_TOP,
        yaxis=y_axis,
    )
    fig.update_xaxes(range=[now - panel.view, now])
    return fig


def validity_help(panel: TimeseriesPanel, metadata: Mapping[str, Metadata]) -> str | None:
    """Writes the help text of the hiding switch: the fill value and valid range of each series, one line per series.

    Args:
        panel: The panel settings.
        metadata: The metadata record of each dataset of the panel, by dataset ID; missing or empty if it has none.

    Returns:
        The help text; None if no series has a fill value or bound, so that the switch would change nothing.
    """
    lines = ["Hides fill values and values outside the valid range:"]
    any_known = False
    for series in panel.series:
        record = metadata.get(series.dataset, {})
        any_known = any_known or valid_range(record, series.field, series.valid_min, series.valid_max).is_known
        description = describe_validity(record, series.field, series.valid_min, series.valid_max)
        lines.append(f"{field_label(record, series.field, series.label)}: {description}")
    return HELP_LINE_BREAK.join(lines) if any_known else None


def _load_frames(panel: TimeseriesPanel) -> dict[str, pd.DataFrame]:
    """Reads the history of every dataset of the panel once, with all fields its series need."""
    fields_by_dataset: dict[str, list[str]] = {}
    for series in panel.series:
        fields_by_dataset.setdefault(series.dataset, []).append(series.field)
    return {dataset: data.read_recent(collection_of(dataset), panel.history, tuple(fields)) for dataset, fields in fields_by_dataset.items()}


def render_timeseries(panel: TimeseriesPanel, context: PanelContext, key: str) -> None:
    """Shows the chart, or a note if none of its datasets holds documents.

    Every drawing replaces the figure and with it a zoom of the viewer, so zooming and panning into the loaded history
    is meant for paused live updates.

    Args:
        panel: The panel settings.
        context: The context of the drawing, whose time ends the view.
        key: The key of the chart element, unique on the page.
    """
    frames = _load_frames(panel)
    metadata = {dataset: data.get_metadata(dataset) for dataset in frames}
    if all(frame.empty for frame in frames.values()):
        with st.container(border=True, height=PANEL_HEIGHT):
            st.info(f"{panel.title or 'Chart'}: no data stored for {sorted(frames)}.")
        return
    # The switch is shown only where a fill value or bound is known, elsewhere it would change nothing
    switch_help = validity_help(panel, metadata)
    hide_invalid = False
    if switch_help is not None:
        hide_invalid = st.toggle("Hide invalid values", value=panel.hide_invalid, key=f"{key}-hide-invalid", help=switch_help)
    st.plotly_chart(timeseries_figure(panel, frames, metadata, context.now, hide_invalid=hide_invalid), width="stretch", key=key)
    newest = {dataset: frame.index.max().to_pydatetime() for dataset, frame in frames.items() if not frame.empty}
    state = panel_freshness(newest, metadata, context.feed_intervals, context.now)
    st.caption(f"{state} -- {sources_line(metadata[series.dataset] for series in panel.series)}")
