import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from space_weather_analysis.dashboard.scales import LEVEL_COLORS
from space_weather_analysis.scales.noaa import LEVEL_NAMES, flare_class, g_level, r_level, s_level
from streamlit_app.figures.scale_style import MISSING_LEVEL_COLOR

# A newest document as read from the time series repository: its UTC time stamp and its data fields.
LatestDocument = tuple[datetime, dict[str, Any]]


@dataclass(frozen=True)
class Tile:
    """The content of one key figure tile of the dashboard.

    Attributes:
        title: What the tile shows.
        value: The newest value as display text, "n/a" without a value.
        time: The UTC time stamp of the newest value; None without a value.
        level: The NOAA scale level with its name; None for figures without a scale or without a value.
        color: The color of the level.
        note: A further line below the value; None shows none.
    """

    title: str
    value: str
    time: datetime | None
    level: str | None = None
    color: str = MISSING_LEVEL_COLOR
    note: str | None = None


def _number(latest: LatestDocument | None, field: str) -> tuple[datetime | None, float | None]:
    """Returns the time stamp and the field value of a newest document; the value is None if it is missing or not a number."""
    if latest is None:
        return None, None
    time, fields = latest
    value = fields.get(field)
    # bool is a subclass of int but no measurement
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return time, None
    return time, float(value)


def _level_text(scale_letter: str, levels: pd.Series) -> tuple[str, str]:
    """Names the level of a one-value level Series with the scale letter and returns it with its color."""
    level = int(levels.iloc[0])
    return f"{scale_letter}{level} {LEVEL_NAMES[level]}", LEVEL_COLORS[level]


def kp_tile(
    latest: LatestDocument | None, *, kp_column: str = "Kp", estimate: LatestDocument | None = None, estimate_column: str = "estimated_kp"
) -> Tile:
    """Shows the newest Kp index with its NOAA geomagnetic storm level, from the estimate if one is given.

    Args:
        latest: The newest official Kp document; None if there is none.
        kp_column: Field holding the official Kp value.
        estimate: The newest estimated Kp document, shown as current value with the official one as note; None shows the official Kp.
        estimate_column: Field holding the estimated Kp value.

    Returns:
        The tile, Kp with two decimals.
    """
    official_time, official_kp = _number(latest, kp_column)
    if estimate is None:
        return _kp_value_tile("Kp index", official_time, official_kp)
    estimate_time, estimated_kp = _number(estimate, estimate_column)
    official = "n/a" if official_kp is None or official_time is None else f"{official_kp:.2f} at {official_time:%Y-%m-%d %H:%M} UTC"
    return _kp_value_tile("Kp index (estimated)", estimate_time, estimated_kp, note=f"Official: {official}")


def _kp_value_tile(title: str, time: datetime | None, kp: float | None, note: str | None = None) -> Tile:
    """Builds a Kp tile with the G level of the value, or n/a without a value."""
    if kp is None:
        return Tile(title, "n/a", time, note=note)
    level, color = _level_text("G", g_level(pd.Series([kp])))
    return Tile(title, f"{kp:.2f}", time, level, color, note)


def xray_tile(latest: LatestDocument | None, *, long_column: str = "Long_Wave") -> Tile:
    """Shows the newest GOES 0.1-0.8 nm X-ray flux as flare class with its NOAA radio blackout level.

    Args:
        latest: The newest X-ray document; None if there is none.
        long_column: Field of the 0.1-0.8 nm channel in W/m^2.

    Returns:
        The tile; a non-positive flux has no flare class and shows "n/a".
    """
    time, flux = _number(latest, long_column)
    flux_series = pd.Series([flux], dtype=float)
    class_name = flare_class(flux_series).iloc[0]
    if class_name is None:
        return Tile("X-ray flare class", "n/a", time)
    level, color = _level_text("R", r_level(flux_series))
    return Tile("X-ray flare class", class_name, time, level, color)


def proton_tile(latest: LatestDocument | None, *, proton_column: str = "P10") -> Tile:
    """Shows the newest GOES >=10 MeV integral proton flux with its NOAA solar radiation storm level.

    Args:
        latest: The newest proton document; None if there is none.
        proton_column: Field of the >=10 MeV channel in pfu (particle flux unit, protons / (cm^2 s sr)).

    Returns:
        The tile, the flux in pfu in scientific notation.
    """
    time, flux = _number(latest, proton_column)
    if flux is None:
        return Tile("Protons >=10 MeV", "n/a", time)
    level, color = _level_text("S", s_level(pd.Series([flux])))
    return Tile("Protons >=10 MeV", f"{flux:.2e} pfu", time, level, color)


def sunspot_tile(latest: LatestDocument | None, *, sunspot_column: str = "sunspot_number") -> Tile:
    """Shows the newest daily sunspot number, which has no NOAA scale.

    Args:
        latest: The newest daily sunspot document; None if there is none.
        sunspot_column: Field holding the sunspot number.

    Returns:
        The tile, the sunspot number as integer.
    """
    time, sunspots = _number(latest, sunspot_column)
    if sunspots is None:
        return Tile("Sunspot number", "n/a", time)
    return Tile("Sunspot number", f"{sunspots:.0f}", time)
