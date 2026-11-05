import pandas as pd
import pytest

from space_weather_analysis.dashboard.labels import Metadata
from space_weather_analysis.dashboard.validity import ValidRange, valid_mask, valid_range

# A record whose field declares fill value and valid range as the providers write them, the fill value as text
RANGED_RECORD: Metadata = {"variables": [{"name": "flux", "fill": "-1e31", "valid_min": 0.0, "valid_max": 100.0}]}


@pytest.mark.parametrize(
    ("metadata", "low", "high", "expected"),
    [
        pytest.param(RANGED_RECORD, None, None, ValidRange(fill=-1e31, low=0.0, high=100.0), id="metadata"),
        pytest.param(RANGED_RECORD, 1e-9, None, ValidRange(fill=-1e31, low=1e-9, high=100.0), id="configured-bound-wins"),
        pytest.param({"variables": [{"name": "flux", "fill": "null"}]}, None, None, ValidRange(), id="fill-no-number"),
        pytest.param({}, None, 5.0, ValidRange(high=5.0), id="configuration-without-metadata"),
    ],
)
def test_valid_range_combines_metadata_and_configuration(metadata: Metadata, low: float | None, high: float | None, expected: ValidRange) -> None:
    """Fill value and bounds come from the metadata, configured bounds take precedence, and text that is no number is no fill value."""
    assert valid_range(metadata, "flux", low, high) == expected


def test_valid_mask_hides_fill_values_and_values_outside_the_bounds() -> None:
    """The fill value and values below or above the bounds are no measurements; missing values stay valid."""
    values = pd.Series([-1e31, -0.5, 0.0, 50.0, 100.0, 100.5, float("nan")])

    mask = valid_mask(values, ValidRange(fill=-1e31, low=0.0, high=100.0))

    assert mask.tolist() == [False, False, True, True, True, False, True]
