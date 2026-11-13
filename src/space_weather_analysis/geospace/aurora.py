import math
from datetime import datetime

import numpy as np
import pandas as pd
from astropy.coordinates import get_sun
from astropy.time import Time

# North pole of the geomagnetic dipole, the centred tilted dipole closest to the field of Earth, in 2025 in geocentric
# latitude (degrees) from the World Magnetic Model 2025 (NOAA NCEI, https://www.ncei.noaa.gov/products/wandering-geomagnetic-poles);
# geomagnetic latitude counts from the equator of this dipole
DIPOLE_POLE_LATITUDE = 80.79
# Geocentric longitude of the same pole in degrees, east positive
DIPOLE_POLE_LONGITUDE = -72.76
# Local times counted as the night side, where the aurora is watched, in hours: 20 to 4 o'clock
NIGHT_START_HOUR = 20.0
NIGHT_END_HOUR = 4.0
# Local time at the subsolar longitude in hours
NOON_HOUR = 12.0
# Hours of a day
DAY_HOURS = 24.0
# Turn of Earth in degrees of longitude per hour of local time
DEGREES_PER_HOUR = 360.0 / DAY_HOURS


def wrap_longitude[T: (float, np.ndarray)](longitude: T) -> T:
    """Wraps longitudes in degrees into -180 to 180, east positive, 180 itself becoming -180."""
    return (longitude + 180.0) % 360.0 - 180.0


def geomagnetic_latitude(latitude: np.ndarray, longitude: np.ndarray) -> np.ndarray:
    """Converts geographic places into their latitude from the equator of the geomagnetic dipole.

    Args:
        latitude: The geographic latitudes in degrees.
        longitude: The geographic longitudes in degrees, east positive.

    Returns:
        The geomagnetic latitudes in degrees, north positive.
    """
    pole_latitude = math.radians(DIPOLE_POLE_LATITUDE)
    latitudes = np.radians(latitude)
    # Spherical law of cosines for the angular distance to the pole, whose cosine is the sine of the geomagnetic latitude
    sine = np.sin(latitudes) * math.sin(pole_latitude) + np.cos(latitudes) * math.cos(pole_latitude) * np.cos(
        np.radians(longitude - DIPOLE_POLE_LONGITUDE)
    )
    return np.degrees(np.arcsin(np.clip(sine, -1.0, 1.0)))


def subsolar_point(time: datetime) -> tuple[float, float]:
    """Computes where the Sun stands overhead at a time.

    Args:
        time: The time, timezone-aware.

    Returns:
        The geographic latitude and longitude in degrees, east positive.
    """
    moment = Time(time)
    sun = get_sun(moment)
    # The Sun stands over the longitude where its right ascension equals the local sidereal time
    longitude = wrap_longitude(sun.ra.deg - moment.sidereal_time("apparent", "greenwich").deg)
    return float(sun.dec.deg), float(longitude)


def night_side_edge(
    latitude: np.ndarray, longitude: np.ndarray, probability: np.ndarray, subsolar_longitude: float, threshold: float, north: bool
) -> float | None:
    """Finds how close to the equator the aurora reaches on the night side of one hemisphere, in geomagnetic latitude.

    Args:
        latitude: The geographic latitudes of the cells in degrees.
        longitude: The geographic longitudes of the cells in degrees, east positive.
        probability: The probability of aurora of each cell in percent.
        subsolar_longitude: The longitude where the Sun stands overhead in degrees, which sets the local time.
        threshold: The lowest probability counted as aurora in percent.
        north: Whether the northern hemisphere is searched, else the southern one.

    Returns:
        The edge as the absolute geomagnetic latitude in degrees; None if the night side holds no such cell.
    """
    magnetic = geomagnetic_latitude(latitude, longitude)
    # Local solar time: noon at the subsolar longitude, one hour per DEGREES_PER_HOUR east of it
    local_hour = (NOON_HOUR + (longitude - subsolar_longitude) / DEGREES_PER_HOUR) % DAY_HOURS
    # The night wraps around midnight, so either bound suffices
    night = (local_hour >= NIGHT_START_HOUR) | (local_hour <= NIGHT_END_HOUR)
    side = magnetic > 0 if north else magnetic < 0
    chosen = night & side & (probability >= threshold)
    if not chosen.any():
        return None
    # The aurora cell closest to the geomagnetic equator per longitude, and the median over the night-side longitudes
    edges = pd.Series(np.abs(magnetic[chosen])).groupby(longitude[chosen]).min()
    return float(edges.median())
