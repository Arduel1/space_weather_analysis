import numpy as np
import pandas as pd
import plotly.graph_objects as go

from streamlit_app.figures.goes import proton_figure, xray_figure
from streamlit_app.figures.kp import kp_figure
from streamlit_app.figures.scale_style import LEVEL_COLORS, MISSING_LEVEL_COLOR
from streamlit_app.figures.solar_activity import cme_table_frame, sunspot_figure


def _utc_index(periods: int, freq: str) -> pd.DatetimeIndex:
    return pd.date_range("2024-05-10", periods=periods, freq=freq, tz="UTC")


def _line_count(fig: go.Figure) -> int:
    return sum(1 for shape in fig.layout.shapes if shape.type == "line")


def test_kp_figure_colors_bars_by_g_level_and_draws_five_thresholds() -> None:
    """Each bar takes the color of its G level, a missing Kp is gray, and duplicate times are dropped."""
    frame = pd.DataFrame({"Kp": [2.0, 5.0, 9.0, np.nan]}, index=_utc_index(4, "3h"))
    frame = pd.concat([frame, frame.iloc[:1].assign(Kp=8.0)])

    fig = kp_figure(frame)

    assert len(fig.data) == 1
    assert list(fig.data[0].marker.color) == [LEVEL_COLORS[0], LEVEL_COLORS[1], LEVEL_COLORS[5], MISSING_LEVEL_COLOR]
    assert _line_count(fig) == 5


def test_xray_figure_has_both_channels_flare_bands_and_r_thresholds() -> None:
    """Both channels are drawn on a log axis with four flare class bands, five R lines and class hover text."""
    frame = pd.DataFrame({"Long_Wave": [1e-6, 5.27e-5, -1.0], "Short_Wave": [1e-7, 2e-6, 3e-7]}, index=_utc_index(3, "min"))

    fig = xray_figure(frame)

    assert [trace.type for trace in fig.data] == ["scattergl", "scattergl"]
    assert fig.layout.yaxis.type == "log"
    assert sum(1 for shape in fig.layout.shapes if shape.type == "rect") == 4
    assert _line_count(fig) == 5
    assert list(fig.data[0].customdata) == ["C1.0", "M5.2", ""]


def test_proton_figure_draws_five_s_thresholds() -> None:
    """The >=10 MeV channel is one log line with a line per S level."""
    frame = pd.DataFrame({"P10": [0.5, 12.0, 0.0]}, index=_utc_index(3, "5min"))

    fig = proton_figure(frame)

    assert len(fig.data) == 1
    assert fig.layout.yaxis.type == "log"
    assert _line_count(fig) == 5


def test_sunspot_figure_plots_monthly_and_optional_smoothed_numbers() -> None:
    """The monthly numbers are drawn as stored; a smoothed frame adds a second trace."""
    months = pd.date_range("2024-01-01", periods=3, freq="MS", tz="UTC")
    monthly = pd.DataFrame({"sunspot_number": [120.5, np.nan, 98.0]}, index=months)
    smoothed = pd.DataFrame({"sunspot_number": [110.0, 108.2, np.nan]}, index=months)

    with_smoothed = sunspot_figure(monthly, smoothed)
    monthly_only = sunspot_figure(monthly)

    assert len(with_smoothed.data) == 2
    assert list(with_smoothed.data[1].y[:2]) == [110.0, 108.2]
    assert len(monthly_only.data) == 1
    assert list(monthly_only.data[0].y[::2]) == [120.5, 98.0]


def test_cme_table_frame_keeps_newest_events_with_display_labels() -> None:
    """The newest events come first, limited to the count, with labeled columns that exist in the frame."""
    frame = pd.DataFrame({"linear_speed": [400.0, 900.0, 1500.0], "is_halo": [False, True, True]}, index=_utc_index(3, "D"))

    table = cme_table_frame(frame, count=2)

    assert list(table.columns) == ["Time (UTC)", "Speed (km/s)", "Halo"]
    assert table["Speed (km/s)"].tolist() == [1500.0, 900.0]
