from datetime import UTC, datetime

import pytest

from streamlit_app.figures.scale_style import LEVEL_COLORS, MISSING_LEVEL_COLOR
from streamlit_app.figures.tiles import Tile, kp_tile, proton_tile, sunspot_tile, xray_tile

TIME = datetime(2024, 5, 10, 18, tzinfo=UTC)


@pytest.mark.parametrize(
    ("tile", "expected"),
    [
        pytest.param(kp_tile((TIME, {"Kp": 4.667})), Tile("Kp index", "4.67", TIME, "G1 Minor", LEVEL_COLORS[1]), id="kp-storm"),
        pytest.param(
            xray_tile((TIME, {"Long_Wave": 5.27e-5})), Tile("X-ray flare class", "M5.2", TIME, "R2 Moderate", LEVEL_COLORS[2]), id="xray-flare"
        ),
        pytest.param(xray_tile((TIME, {"Long_Wave": 0.0})), Tile("X-ray flare class", "n/a", TIME), id="xray-non-positive"),
        pytest.param(
            proton_tile((TIME, {"P10": 0.18})), Tile("Protons >=10 MeV", "1.80e-01 pfu", TIME, "S0 None", LEVEL_COLORS[0]), id="protons-quiet"
        ),
        pytest.param(sunspot_tile((TIME, {"sunspot_number": 24})), Tile("Sunspot number", "24", TIME), id="sunspots-no-scale"),
        pytest.param(kp_tile((TIME, {"status": "def"})), Tile("Kp index", "n/a", TIME), id="field-missing"),
        pytest.param(proton_tile(None), Tile("Protons >=10 MeV", "n/a", None, None, MISSING_LEVEL_COLOR), id="no-document"),
    ],
)
def test_tile_shows_newest_value_with_noaa_level(tile: Tile, expected: Tile) -> None:
    """A tile shows the formatted newest value with its scale level, or n/a without a usable value."""
    assert tile == expected
