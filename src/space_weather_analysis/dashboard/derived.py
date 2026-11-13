from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType

import numpy as np
import pandas as pd

# Distance from Earth to the L1 Lagrange point, where the solar wind monitors fly, in km: L1 lies sunward of Earth where
# the pull of Sun and Earth keeps a spacecraft in step with Earth, about 1.5 million km away (Stone, E. C. et al.
# (1998), The Advanced Composition Explorer, Space Sci. Rev. 86, 1, doi:10.1023/A:1005082526237); the payload names
# this rounded value in reference and formula
L1_EARTH_DISTANCE_KM = 1.5e6
# Mean distance from the Sun to Earth in km, one astronomical unit (149597870.7 km by IAU 2012 Resolution B2,
# https://www.iau.org/static/resolutions/IAU2012_English.pdf) rounded to four digits, as reference and formula of the
# payload name it
SUN_EARTH_DISTANCE_KM = 1.496e8
# Geomagnetic latitude in degrees of the equatorward edge of the auroral oval at Kp 0, by the rule of thumb of NOAA SWPC
# (Tips on Viewing the Aurora, https://www.spaceweather.gov/content/tips-viewing-aurora)
AURORA_EDGE_AT_KP0 = 66
# Degrees the edge moves towards the equator per Kp level, by the same rule of thumb
AURORA_EDGE_SHIFT_PER_KP = 2


@dataclass(frozen=True)
class DerivedQuantity:
    """A quantity computed value by value from measured quantities with a published formula.

    Attributes:
        inputs: The names of the measured quantities the formula takes, in its argument order.
        compute: The formula, applied to aligned Series of the inputs.
        label: The name shown for the quantity.
        units: The unit of the result.
        reference: The publication the formula comes from.
        formula: The formula as the publication writes it, in plain text with ^ for powers and _ for indices.
    """

    inputs: tuple[str, ...]
    compute: Callable[..., pd.Series]
    label: str
    units: str
    reference: str
    formula: str


def _l1_travel_minutes(speed: pd.Series) -> pd.Series:
    """Returns the minutes solar wind of the given speed in km/s takes from L1 to Earth."""
    # Constant speed over the distance; a non-positive speed gives no travel time
    return L1_EARTH_DISTANCE_KM / speed.where(speed > 0) / timedelta(minutes=1).total_seconds()


def _sun_earth_travel_days(speed: pd.Series) -> pd.Series:
    """Returns the days something at the given constant speed in km/s takes from the Sun to Earth."""
    # Constant speed over the distance; a non-positive speed gives no travel time
    return SUN_EARTH_DISTANCE_KM / speed.where(speed > 0) / timedelta(days=1).total_seconds()


def _aurora_oval_edge(kp: pd.Series) -> pd.Series:
    """Returns the approximate geomagnetic latitude of the equatorward edge of the auroral oval in degrees from Kp."""
    return AURORA_EDGE_AT_KP0 - AURORA_EDGE_SHIFT_PER_KP * kp


# Derived quantities a series or tile can name, by name
DERIVED: Mapping[str, DerivedQuantity] = MappingProxyType(
    {
        "aurora_oval_edge": DerivedQuantity(
            ("kp",),
            _aurora_oval_edge,
            "Aurora oval reaches about",
            "deg geomagnetic latitude",
            "NOAA SWPC, Tips on Viewing the Aurora, https://www.spaceweather.gov/content/tips-viewing-aurora (66 deg at Kp 0, 2 deg per Kp level)",
            "latitude = 66 - 2 Kp",
        ),
        "l1_travel_time": DerivedQuantity(
            ("speed",), _l1_travel_minutes, "Travel time L1 to Earth", "min", "Distance L1 to Earth about 1.5 million km", "t = 1.5e6 km / V"
        ),
        "sun_earth_travel_time": DerivedQuantity(
            ("speed",),
            _sun_earth_travel_days,
            "Sun to Earth at this speed",
            "days",
            "Mean Sun-Earth distance 1 au = 1.496e8 km",
            "t = 1.496e8 km / V",
        ),
    }
)


# Step of the common time grid derived quantities are computed on
ALIGN_RESOLUTION = "1min"


def align(columns: Mapping[str, pd.Series], resolution: str = ALIGN_RESOLUTION) -> pd.DataFrame:
    """Joins series of different datasets on one time grid, the mean of each series per step of the resolution.

    Args:
        columns: The series by input name, each with a UTC DatetimeIndex.
        resolution: The step of the common time grid as pandas frequency text.

    Returns:
        One column per input; rows where any input is missing are dropped.
    """
    # Values of one dataset within one step are averaged, so that datasets of different cadence meet on the same times
    gridded = {
        name: pd.to_numeric(series, errors="coerce").groupby(pd.DatetimeIndex(series.index).floor(resolution)).mean()
        for name, series in columns.items()
    }
    return pd.DataFrame(gridded).dropna()


def compute(name: str, columns: Mapping[str, pd.Series]) -> pd.Series:
    """Computes a derived quantity from its input series aligned on a one-minute grid.

    Args:
        name: A key of DERIVED.
        columns: The input series by input name.

    Returns:
        The derived values with a UTC DatetimeIndex; empty if an input has no value.
    """
    quantity = DERIVED[name]
    aligned = align({input_name: columns[input_name] for input_name in quantity.inputs})
    if aligned.empty:
        return pd.Series(dtype=float)
    # A formula dividing by zero gives infinity, which counts as missing like any other undefined result
    return quantity.compute(*(aligned[input_name] for input_name in quantity.inputs)).replace([np.inf, -np.inf], np.nan)
