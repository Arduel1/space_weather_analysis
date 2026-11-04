from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from streamlit_app.dashboard.labels import Metadata
from streamlit_app.dashboard.panels.table import TIME_LABEL, TablePanel, table_frame
from streamlit_app.dashboard.panels.timeseries import TimeseriesPanel, timeseries_figure
from streamlit_app.dashboard.panels.value import ValuePanel, ValueView, value_view
from streamlit_app.dashboard.scales import Level, threshold_scale

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


def _value_panel(**settings: Any) -> ValuePanel:
    """Builds a value panel of the field `flux` with the given settings."""
    return ValuePanel.model_validate({"type": "value", "row": 1, "dataset": "a_set", "field": "flux", **settings})


def _unlabelled_view(text: str, time: datetime | None, level: Level | None) -> ValueView:
    """Builds the view of the field `flux` of a dataset without metadata."""
    return ValueView("flux", text, time, level, source=NO_SOURCE)


@pytest.mark.parametrize(
    ("panel", "latest", "expected"),
    [
        pytest.param(
            _value_panel(format="{:.1e}", unit="W/m²", scale="noaa_r"),
            (VALUE_TIME, {"flux": 2e-5}),
            _unlabelled_view("2.0e-05 W/m²", VALUE_TIME, Level(1e-5, "R1 Minor", "#F1C40F")),
            id="format-string-unit-and-noaa-level",
        ),
        pytest.param(
            _value_panel(format="flare_class", scale="noaa_r"),
            (VALUE_TIME, {"flux": 5.27e-5}),
            _unlabelled_view("M5.2", VALUE_TIME, Level(5e-5, "R2 Moderate", "#E67E22")),
            id="named-formatter",
        ),
        pytest.param(
            _value_panel(thresholds=[{"value": 10.0, "label": "high", "color": "red"}]),
            (VALUE_TIME, {"flux": 15.0}),
            _unlabelled_view("15", VALUE_TIME, Level(10.0, "high", "red")),
            id="own-thresholds",
        ),
        pytest.param(_value_panel(scale="noaa_r"), (VALUE_TIME, {"other": 1.0}), _unlabelled_view("n/a", VALUE_TIME, None), id="missing-field"),
        pytest.param(_value_panel(scale="noaa_r"), (VALUE_TIME, {"flux": True}), _unlabelled_view("n/a", VALUE_TIME, None), id="bool-value"),
        pytest.param(_value_panel(scale="noaa_r"), None, _unlabelled_view("n/a", None, None), id="no-document"),
        pytest.param(
            _value_panel(format="{:.2f}", scale="noaa_g"),
            (VALUE_TIME, {"flux": 2.0}),
            _unlabelled_view("2.00", VALUE_TIME, Level(0.0, "G0 None", "#2ECC71")),
            id="quiet-level",
        ),
    ],
)
def test_value_view_formats_the_newest_value_with_its_level(
    panel: ValuePanel, latest: tuple[datetime, dict[str, Any]] | None, expected: ValueView
) -> None:
    """The tile shows the formatted value with unit and level, and "n/a" without level for no usable value."""
    assert value_view(panel, latest, {}) == expected


@pytest.mark.parametrize(
    ("panel", "expected"),
    [
        pytest.param(
            _value_panel(format="{:.1e}", title="X-ray", unit="Wm-2"),
            ValueView(
                "X-ray",
                "2.0e-05 Wm-2",
                VALUE_TIME,
                None,
                help="Flux of the long channel -- Synthetic X-ray flux",
                source="Source: Synthetic provider, CC0",
            ),
            id="configured-title-and-unit-win",
        ),
        pytest.param(
            _value_panel(format="{:.1e}"),
            ValueView(
                "Long flux",
                "2.0e-05 W/m^2",
                VALUE_TIME,
                None,
                help="Flux of the long channel -- Synthetic X-ray flux",
                source="Source: Synthetic provider, CC0",
            ),
            id="metadata-label-and-unit",
        ),
        pytest.param(
            _value_panel(format="flare_class"),
            ValueView(
                "Long flux",
                "M2.0",
                VALUE_TIME,
                None,
                help="Flux of the long channel -- Synthetic X-ray flux",
                source="Source: Synthetic provider, CC0",
            ),
            id="named-formatter-without-unit",
        ),
    ],
)
def test_value_view_labels_the_tile_from_configuration_else_metadata(panel: ValuePanel, expected: ValueView) -> None:
    """The tile takes title and unit from the configuration, else from the metadata, no unit after a named formatter, with help and source."""
    assert value_view(panel, (VALUE_TIME, {"flux": 2e-5}), FLUX_RECORD) == expected


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

    figure = timeseries_figure(panel, frames, {}, NOW)

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

    table = table_frame(panel, frame, {})

    expected = pd.DataFrame({TIME_LABEL: TIMES[::-1][:2], "Speed (km/s)": [300.0, 200.0], "remarks": ["third", "second"]})
    pd.testing.assert_frame_equal(table, expected)


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


def test_table_frame_labels_unconfigured_columns_from_metadata() -> None:
    """A column without configured label is headed by the label of its variable from the metadata."""
    panel = TablePanel.model_validate({"type": "table", "row": 1, "dataset": "a_set", "history": "P1D", "columns": [{"field": "flux"}]})
    frame = pd.DataFrame({"flux": [1e-6, 2e-6, 3e-6]}, index=TIMES)

    table = table_frame(panel, frame, FLUX_RECORD)

    assert table.columns.tolist() == [TIME_LABEL, "Long flux"]
