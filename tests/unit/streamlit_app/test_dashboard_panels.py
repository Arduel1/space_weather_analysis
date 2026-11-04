from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from streamlit_app.dashboard.panels.table import TIME_LABEL, TablePanel, table_frame
from streamlit_app.dashboard.panels.timeseries import TimeseriesPanel, timeseries_figure
from streamlit_app.dashboard.panels.value import ValuePanel, ValueView, value_view
from streamlit_app.dashboard.scales import Level, threshold_scale

NOW = datetime(2024, 1, 1, 3, tzinfo=UTC)
VALUE_TIME = datetime(2024, 1, 1, 2, tzinfo=UTC)
TIMES = pd.DatetimeIndex(pd.to_datetime(["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "2024-01-01T02:00:00Z"], utc=True), name="timestamp")


def _value_panel(**settings: Any) -> ValuePanel:
    """Builds a value panel of the field `flux` with the given settings."""
    return ValuePanel.model_validate({"type": "value", "row": 1, "dataset": "a_set", "field": "flux", **settings})


@pytest.mark.parametrize(
    ("panel", "latest", "expected"),
    [
        pytest.param(
            _value_panel(format="{:.1e}", unit="W/m²", scale="noaa_r"),
            (VALUE_TIME, {"flux": 2e-5}),
            ValueView("2.0e-05 W/m²", VALUE_TIME, Level(1e-5, "R1 Minor", "#F1C40F")),
            id="format-string-unit-and-noaa-level",
        ),
        pytest.param(
            _value_panel(format="flare_class", scale="noaa_r"),
            (VALUE_TIME, {"flux": 5.27e-5}),
            ValueView("M5.2", VALUE_TIME, Level(5e-5, "R2 Moderate", "#E67E22")),
            id="named-formatter",
        ),
        pytest.param(
            _value_panel(thresholds=[{"value": 10.0, "label": "high", "color": "red"}]),
            (VALUE_TIME, {"flux": 15.0}),
            ValueView("15", VALUE_TIME, Level(10.0, "high", "red")),
            id="own-thresholds",
        ),
        pytest.param(_value_panel(scale="noaa_r"), (VALUE_TIME, {"other": 1.0}), ValueView("n/a", VALUE_TIME, None), id="missing-field"),
        pytest.param(_value_panel(scale="noaa_r"), (VALUE_TIME, {"flux": True}), ValueView("n/a", VALUE_TIME, None), id="bool-value"),
        pytest.param(_value_panel(scale="noaa_r"), None, ValueView("n/a", None, None), id="no-document"),
        pytest.param(
            _value_panel(format="{:.2f}", scale="noaa_g"),
            (VALUE_TIME, {"flux": 2.0}),
            ValueView("2.00", VALUE_TIME, Level(0.0, "G0 None", "#2ECC71")),
            id="quiet-level",
        ),
    ],
)
def test_value_view_formats_the_newest_value_with_its_level(
    panel: ValuePanel, latest: tuple[datetime, dict[str, Any]] | None, expected: ValueView
) -> None:
    """The tile shows the formatted value with unit and level, and "n/a" without level for no usable value."""
    assert value_view(panel, latest) == expected


def test_threshold_scale_counts_the_reached_thresholds() -> None:
    """Values below the first threshold get 0, a value equal to a threshold reaches it, and NaN gets NA."""
    scale = threshold_scale([Level(1.0, "low", "green"), Level(10.0, "mid", "orange"), Level(100.0, "high", "red")])

    levels = scale.classify(pd.Series([0.5, 1.0, 5.0, 10.0, 1000.0, np.nan]))

    assert levels.tolist() == [0, 1, 1, 2, 3, pd.NA]


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

    figure = timeseries_figure(panel, frames, NOW)

    assert (
        [trace.type for trace in figure.data],
        len(figure.layout.shapes),
        np.isnan(figure.data[0].y).tolist(),
        figure.layout.xaxis.range,
        figure.layout.yaxis.range,
    ) == (["bar", "scattergl"], 5, [True, True, False], (NOW - timedelta(hours=6), NOW), (-1.0, 5.0))


def test_table_frame_shows_the_newest_documents_first() -> None:
    """The table holds the newest documents up to its count, the time first, labelled columns and no missing column."""
    panel = TablePanel.model_validate(
        {
            "type": "table",
            "row": 1,
            "dataset": "a_set",
            "history": "P1D",
            "count": 2,
            "columns": [{"field": "speed", "label": "Speed (km/s)"}, {"field": "absent"}, {"field": "remarks"}],
        }
    )
    frame = pd.DataFrame({"speed": [100.0, 200.0, 300.0], "remarks": ["first", "second", "third"]}, index=TIMES)

    table = table_frame(panel, frame)

    expected = pd.DataFrame({TIME_LABEL: TIMES[::-1][:2], "Speed (km/s)": [300.0, 200.0], "remarks": ["third", "second"]})
    pd.testing.assert_frame_equal(table, expected)
