from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
import pandas as pd

from space_weather_analysis.scales.noaa import (
    G_THRESHOLDS_KP,
    LEVEL_NAMES,
    R_THRESHOLDS_W_M2,
    S_THRESHOLDS_PFU,
    flare_class,
    g_level,
    r_level,
    s_level,
)
from streamlit_app.figures.scale_style import LEVEL_COLORS


@dataclass(frozen=True)
class Level:
    """One level of a scale.

    Attributes:
        value: The lowest value of the level.
        label: The name shown for the level.
        color: The color of the level.
    """

    value: float
    label: str
    color: str


@dataclass(frozen=True)
class Scale:
    """Levels that classify the values of a field, drawn as threshold lines and shown as badge.

    Attributes:
        levels: The levels from the lowest up, level number 1 first.
        classify: Maps values to their level number, 0 below the first level and NA for missing values.
        quiet: The level shown for values below the first level; None shows no badge for them.
    """

    levels: tuple[Level, ...]
    classify: Callable[[pd.Series], pd.Series]
    quiet: Level | None = None

    def level_of(self, value: float) -> Level | None:
        """Returns the level of one value, the quiet level below the first one; None without level."""
        number = self.classify(pd.Series([value], dtype=float)).iloc[0]
        if pd.isna(number):
            return None
        return self.quiet if number == 0 else self.levels[int(number) - 1]


def threshold_scale(levels: Sequence[Level]) -> Scale:
    """Builds a scale whose level is the number of level values a value reaches.

    Args:
        levels: The levels, sorted by ascending value.

    Returns:
        The scale without quiet level.
    """
    values = np.array([level.value for level in levels], dtype=float)

    def classify(series: pd.Series) -> pd.Series:
        numbers = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float, na_value=np.nan)
        return pd.Series(np.searchsorted(values, numbers, side="right"), index=series.index, dtype="Int64").mask(~np.isfinite(numbers))

    return Scale(tuple(levels), classify)


def _noaa_scale(letter: str, thresholds: Sequence[float] | np.ndarray, classify: Callable[[pd.Series], pd.Series]) -> Scale:
    """Builds one of the NOAA space weather scales with its level names and colors."""
    levels = tuple(
        Level(float(value), f"{letter}{number} {LEVEL_NAMES[number]}", LEVEL_COLORS[number]) for number, value in enumerate(thresholds, start=1)
    )
    return Scale(levels, classify, Level(0.0, f"{letter}0 {LEVEL_NAMES[0]}", LEVEL_COLORS[0]))


# Scales a panel can name, by name; the NOAA scales follow https://www.swpc.noaa.gov/noaa-scales-explanation
SCALES: Mapping[str, Scale] = MappingProxyType(
    {
        "noaa_g": _noaa_scale("G", G_THRESHOLDS_KP, g_level),
        "noaa_s": _noaa_scale("S", S_THRESHOLDS_PFU, s_level),
        "noaa_r": _noaa_scale("R", R_THRESHOLDS_W_M2, r_level),
    }
)


def _flare_class_text(value: float) -> str:
    """Names the GOES flare class of a 0.1-0.8 nm X-ray flux in W/m^2, n/a for a non-positive flux."""
    return flare_class(pd.Series([value], dtype=float)).iloc[0] or "n/a"


# Formats a panel can name instead of a Python format string, by name
FORMATTERS: Mapping[str, Callable[[float], str]] = MappingProxyType({"flare_class": _flare_class_text})


def format_value(value: float, value_format: str) -> str:
    """Formats a value with a named formatter or a Python format string with one field.

    Args:
        value: The value.
        value_format: A key of FORMATTERS or a format string like `{:.2f}`.

    Returns:
        The text.
    """
    formatter = FORMATTERS.get(value_format)
    return formatter(value) if formatter is not None else value_format.format(value)
