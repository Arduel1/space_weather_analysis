from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from space_weather_analysis.dashboard.labels import Metadata
from space_weather_analysis.dashboard.panels.timeseries import TimeseriesPanel
from streamlit_app.dashboard.panels.timeseries import timeseries_figure, validity_help

NOW = datetime(2024, 1, 1, 3, tzinfo=UTC)
VALUE_TIME = datetime(2024, 1, 1, 2, tzinfo=UTC)
TIMES = pd.DatetimeIndex(pd.to_datetime(["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "2024-01-01T02:00:00Z"], utc=True), name="timestamp")
NO_SOURCE = "Source: no metadata"
FLUX_RECORD: Metadata = {
    "source": {"type": "SWPC", "provider": "Synthetic provider"},
    "title": "Synthetic X-ray flux",
    "citation": {"license": "CC0"},
    "variables": [{"name": "flux", "label": "Long flux", "units": "W/m^2", "description": "Flux of the long channel"}],
}


def test_timeseries_figure_draws_series_levels_and_the_view() -> None:
    """Each series gets a trace of its style, each scale level a line, and a log axis drops non-positive values and takes exponents."""
    panel = TimeseriesPanel.model_validate(
        {
            "type": "timeseries",
            "row": 1,
            "view": "PT6H",
            "history": "P1D",
            "y_axis": "log",
            "y_range": [0.1, 1e5],
            "scale": "noaa_s",
            "series": [{"dataset": "a_set", "field": "flux", "style": "bars"}, {"dataset": "a_set", "field": "flux"}],
        }
    )
    frames = {"a_set": pd.DataFrame({"flux": [-1.0, 0.0, 5.0]}, index=TIMES)}

    figure = timeseries_figure(panel, frames, {}, NOW)

    assert (
        [trace.type for trace in figure.data],
        len(figure.layout.shapes),
        np.isnan(figure.data[0].y).tolist(),
        figure.layout.xaxis.range,
        figure.layout.yaxis.range,
    ) == (["bar", "scattergl"], 5, [True, True, False], (NOW - timedelta(hours=6), NOW), (-1.0, 5.0))


def test_timeseries_figure_labels_series_and_axes_from_metadata() -> None:
    """Without configuration, series name, hover unit, axis title and figure title come from the metadata; a configured label wins."""
    panel = TimeseriesPanel.model_validate(
        {
            "type": "timeseries",
            "row": 1,
            "view": "PT6H",
            "history": "P1D",
            "series": [{"dataset": "a_set", "field": "flux"}, {"dataset": "b_set", "field": "speed", "label": "Speed"}],
        }
    )
    frames = {"a_set": pd.DataFrame({"flux": [1e-6, 2e-6, 3e-6]}, index=TIMES), "b_set": pd.DataFrame({"speed": [400.0, 410.0, 420.0]}, index=TIMES)}

    figure = timeseries_figure(panel, frames, {"a_set": FLUX_RECORD}, NOW)

    assert (
        [trace.name for trace in figure.data],
        [trace.hovertemplate for trace in figure.data],
        figure.layout.yaxis.title.text,
        figure.layout.title.text,
    ) == (["Long flux", "Speed"], ["%{y:.3g} W/m^2", "%{y:.3g}"], "W/m^2", "Synthetic X-ray flux")


def test_timeseries_figure_hides_invalid_values_on_request() -> None:
    """With hiding on, values outside the configured valid range of a series become gaps; with hiding off they stay."""
    panel = TimeseriesPanel(type="timeseries", row=1, view="PT1H", history="PT1H", series=[{"dataset": "set", "field": "flux", "valid_min": 1e-8}])
    frame = pd.DataFrame({"flux": [1e-9, 2e-8, 3e-8]}, index=pd.DatetimeIndex([NOW - timedelta(minutes=minute) for minute in (2, 1, 0)]))

    hidden = timeseries_figure(panel, {"set": frame}, {}, NOW, hide_invalid=True)
    shown = timeseries_figure(panel, {"set": frame}, {}, NOW, hide_invalid=False)

    assert (bool(pd.isna(hidden.data[0].y[0])), float(shown.data[0].y[0])) == (True, 1e-9)


def _kp_panel(series: list[dict[str, Any]]) -> TimeseriesPanel:
    """Builds a time series panel of the given series over one hour."""
    return TimeseriesPanel(type="timeseries", row=1, view="PT1H", history="PT1H", series=series)


@pytest.mark.parametrize(
    ("series", "expected"),
    [
        pytest.param(
            [{"dataset": "kp", "field": "kp", "label": "Kp", "valid_min": 0, "valid_max": 9}, {"dataset": "other", "field": "x", "label": "X"}],
            "Hides fill values and values outside the valid range:  \nKp: valid 0 (config) to 9 (config)  \nX: no fill value or valid range known",
            id="one-series-with-bounds-lists-every-series",
        ),
        pytest.param([{"dataset": "other", "field": "x", "label": "X"}], None, id="nothing-known-no-switch"),
    ],
)
def test_validity_help_lists_each_series_or_hides_the_switch(series: list[dict[str, Any]], expected: str | None) -> None:
    """The switch help lists every series on its own line, and there is no switch when no series has a fill value or bound."""
    assert validity_help(_kp_panel(series), {}) == expected
