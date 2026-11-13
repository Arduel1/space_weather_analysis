from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from space_weather_analysis.dashboard.labels import Metadata
from space_weather_analysis.dashboard.panels.series import break_gaps
from space_weather_analysis.dashboard.panels.value import ValuePanel, ValueView, value_view
from space_weather_analysis.dashboard.scales import Level, threshold_scale
from space_weather_analysis.dashboard.validity import describe_validity

NOW = datetime(2024, 1, 1, 3, tzinfo=UTC)
VALUE_TIME = datetime(2024, 1, 1, 2, tzinfo=UTC)
FLUX_RECORD: Metadata = {
    "source": {"type": "SWPC", "provider": "Synthetic provider"},
    "title": "Synthetic X-ray flux",
    "citation": {"license": "CC0"},
    "variables": [{"name": "flux", "label": "Long flux", "units": "W/m^2", "description": "Flux of the long channel"}],
}


def _value_panel(**settings: Any) -> ValuePanel:
    """Builds a value panel of the field `flux` with the given settings."""
    return ValuePanel.model_validate({"type": "value", "row": 1, "dataset": "a_set", "field": "flux", **settings})


def _unlabelled_view(text: str, time: datetime | None, level: Level | None, measured: bool = False) -> ValueView:
    """Builds the view of the field `flux` of a dataset without metadata."""
    return ValueView("flux", text, time, level, measured=measured)


@pytest.mark.parametrize(
    ("panel", "latest", "expected"),
    [
        pytest.param(
            _value_panel(format="{:.1e}", unit="W/m²", scale="noaa_r"),
            (VALUE_TIME, {"flux": 2e-5}),
            _unlabelled_view("2.0e-05 W/m²", VALUE_TIME, Level(1e-5, "R1 Minor", "#F1C40F"), measured=True),
            id="format-string-unit-and-noaa-level",
        ),
        pytest.param(
            _value_panel(format="flare_class", scale="noaa_r"),
            (VALUE_TIME, {"flux": 5.27e-5}),
            _unlabelled_view("M5.2", VALUE_TIME, Level(5e-5, "R2 Moderate", "#E67E22"), measured=True),
            id="named-formatter",
        ),
        pytest.param(
            _value_panel(thresholds=[{"value": 10.0, "label": "high", "color": "red"}]),
            (VALUE_TIME, {"flux": 15.0}),
            _unlabelled_view("15", VALUE_TIME, Level(10.0, "high", "red"), measured=True),
            id="own-thresholds",
        ),
        pytest.param(_value_panel(scale="noaa_r"), (VALUE_TIME, {"other": 1.0}), _unlabelled_view("n/a", VALUE_TIME, None), id="missing-field"),
        pytest.param(_value_panel(scale="noaa_r"), (VALUE_TIME, {"flux": True}), _unlabelled_view("n/a", VALUE_TIME, None), id="bool-value"),
        pytest.param(_value_panel(scale="noaa_r"), None, _unlabelled_view("n/a", None, None), id="no-document"),
        pytest.param(
            _value_panel(format="{:.2f}", scale="noaa_g"),
            (VALUE_TIME, {"flux": 2.0}),
            _unlabelled_view("2.00", VALUE_TIME, Level(0.0, "G0 None", "#2ECC71"), measured=True),
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
                help_lines=("Flux of the long channel", "Dataset: Synthetic X-ray flux"),
                measured=True,
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
                help_lines=("Flux of the long channel", "Dataset: Synthetic X-ray flux"),
                measured=True,
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
                help_lines=("Flux of the long channel", "Dataset: Synthetic X-ray flux"),
                measured=True,
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


# Minutes 0, 1, 2 and, after a missing stretch, 12 and 13 after NOW
GAPPY_MINUTES = [0, 1, 2, 12, 13]


def _minute_values(minutes: list[int]) -> pd.Series:
    """Provides the value 1.0 at each of the given minutes after NOW."""
    return pd.Series(1.0, index=pd.DatetimeIndex([NOW + timedelta(minutes=minute) for minute in minutes]))


@pytest.mark.parametrize(
    ("minutes", "cadence", "expected_break_minutes"),
    [
        pytest.param(GAPPY_MINUTES, None, [7.0], id="median-spacing-finds-gap"),
        pytest.param(GAPPY_MINUTES, timedelta(minutes=5), [], id="coarse-cadence-no-gap"),
        pytest.param([0, 1, 2, 3], None, [], id="regular-values"),
        pytest.param([0], None, [], id="single-value"),
    ],
)
def test_break_gaps_inserts_a_missing_value_halfway_into_each_gap(
    minutes: list[int], cadence: timedelta | None, expected_break_minutes: list[float]
) -> None:
    """A spacing longer than three times the cadence, or the median spacing without cadence, gets one NaN halfway in."""
    broken = break_gaps(_minute_values(minutes), cadence)

    break_minutes = [(time - NOW).total_seconds() / 60 for time in broken.index[broken.isna()]]
    assert (break_minutes, broken.dropna().index.tolist()) == (expected_break_minutes, _minute_values(minutes).index.tolist())


@pytest.mark.parametrize(
    ("metadata", "low", "high", "expected"),
    [
        pytest.param(
            {"variables": [{"name": "flux", "fill": "0", "valid_min": 1e-9, "valid_max": 1.0}]}, None, None, "fill 0, valid 1e-09 to 1", id="metadata"
        ),
        pytest.param({"variables": [{"name": "flux", "fill": "0"}]}, 1e-9, None, "fill 0, valid >= 1e-09 (config)", id="configured-low-bound"),
        pytest.param({}, None, 100.0, "valid <= 100 (config)", id="configured-high-bound"),
        pytest.param({}, None, None, "no fill value or valid range known", id="nothing-known"),
    ],
)
def test_describe_validity_names_fill_value_range_and_configured_bounds(
    metadata: Metadata, low: float | None, high: float | None, expected: str
) -> None:
    """The description names fill value and valid range and marks bounds from the configuration."""
    assert describe_validity(metadata, "flux", low, high) == expected
