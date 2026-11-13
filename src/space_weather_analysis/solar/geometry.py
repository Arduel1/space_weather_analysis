import re

import numpy as np
import pandas as pd

# Julian date (days counted continuously since 4713 BC) of the standard epoch J2000.0, 2000-01-01 12:00 Terrestrial Time,
# from which the series of Meeus count
J2000 = 2451545.0
# Days of a Julian century, the time unit of the series of Meeus
DAYS_PER_CENTURY = 36525.0
# Inclination of the solar equator to the ecliptic in degrees, as Carrington measured it and Meeus uses it
SOLAR_EQUATOR_INCLINATION = 7.25

# Source of the solar position and the B0 angle, shown with the solar disk
MEEUS_REFERENCE = "Meeus, J. (1998). Astronomical Algorithms, 2nd ed., Willmann-Bell, chapters 25 and 29"

# The Sun rotates faster at the equator than near the poles (differential rotation): sidereal rotation of small magnetic
# features in degrees per day by latitude, A + B sin^2(latitude) + C sin^4(latitude) (Howard et al. 1990), as SunPy uses it
ROTATION_A = 14.713
ROTATION_B = -2.396
ROTATION_C = -1.787
# Mean motion of Earth around the Sun in degrees per day, 360 degrees per sidereal year of 365.256 days, by which the
# synodic rotation seen from Earth is slower
EARTH_ORBIT_RATE = 0.9856
# Source of the differential rotation, shown with the solar disk
ROTATION_REFERENCE = "Howard, R. F., Harvey, J. W. and Forgach, S. (1990), Solar Physics 130, 295, doi:10.1007/BF00156795"


def julian_date(times: pd.DatetimeIndex) -> np.ndarray:
    """Converts UTC times to Julian dates."""
    return np.asarray(times.tz_convert("UTC").tz_localize(None).to_julian_date(), dtype=float)


def solar_b0(times: pd.DatetimeIndex) -> np.ndarray:
    """Computes the B0 angle, the heliographic latitude of the centre of the solar disk seen from Earth.

    Heliographic latitude counts from the solar equator; B0 is the tilt of the solar axis towards Earth over the year.

    Args:
        times: The UTC times.

    Returns:
        B0 in degrees, between about -7.25 and 7.25.
    """
    days = julian_date(times)
    # The numeric coefficients are those of Meeus: mean longitude, mean anomaly and equation of centre of chapter 25, node of chapter 29
    centuries = (days - J2000) / DAYS_PER_CENTURY
    mean_longitude = 280.46646 + 36000.76983 * centuries
    mean_anomaly = np.radians(357.52911 + 35999.05029 * centuries)
    # The equation of centre corrects the mean longitude for the elliptic orbit of Earth
    centre = (1.914602 - 0.004817 * centuries) * np.sin(mean_anomaly) + 0.019993 * np.sin(2 * mean_anomaly) + 0.000289 * np.sin(3 * mean_anomaly)
    true_longitude = np.radians(mean_longitude + centre)
    # Ecliptic longitude of the ascending node of the solar equator, counted from Julian date 2396758 (Carrington's epoch, 1850)
    node = np.radians(73.6667 + 1.3958333 * (days - 2396758.0) / DAYS_PER_CENTURY)
    # B0 = asin(sin(true longitude - node) sin(inclination)), Meeus chapter 29
    return np.degrees(np.arcsin(np.sin(true_longitude - node) * np.sin(np.radians(SOLAR_EQUATOR_INCLINATION))))


def synodic_rotation(latitude: np.ndarray) -> np.ndarray:
    """Returns how fast a sunspot at a heliographic latitude moves across the disk seen from Earth, in degrees per day.

    Args:
        latitude: The heliographic latitudes in degrees.

    Returns:
        The synodic rotation rate, the sidereal rate less the orbital motion of Earth, in degrees per day.
    """
    sine_squared = np.sin(np.radians(latitude)) ** 2
    return ROTATION_A + ROTATION_B * sine_squared + ROTATION_C * sine_squared**2 - EARTH_ORBIT_RATE


def disk_position(latitude: np.ndarray, longitude: np.ndarray, b0: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Projects heliographic positions onto the solar disk seen from Earth, the solar north at the top.

    The longitude counts from the central meridian, the meridian facing Earth, as in Stonyhurst heliographic coordinates
    (Thompson 2006, Astronomy & Astrophysics 449, 791, doi:10.1051/0004-6361:20054262); the projection is orthographic.

    Args:
        latitude: The heliographic latitudes in degrees.
        longitude: The longitudes from the central meridian in degrees, positive to the west.
        b0: The tilt of the solar axis towards Earth in degrees.

    Returns:
        The position to the west and to the north as shares of the solar radius from the disk centre, and whether the
        position lies on the visible side.
    """
    latitude_rad, longitude_rad, tilt = np.radians(latitude), np.radians(longitude), np.radians(b0)
    # The unit vector of the position turned by the tilt about the west axis; its third component points towards Earth
    west = np.cos(latitude_rad) * np.sin(longitude_rad)
    north = np.sin(latitude_rad) * np.cos(tilt) - np.cos(latitude_rad) * np.sin(tilt) * np.cos(longitude_rad)
    visible = np.sin(latitude_rad) * np.sin(tilt) + np.cos(latitude_rad) * np.cos(tilt) * np.cos(longitude_rad) > 0
    return west, north, visible


def thinned_line(points: np.ndarray, most: int) -> np.ndarray:
    """Keeps at most a number of points of a line, evenly spread along it and with both ends.

    Args:
        points: The points of the line in order, one per row.
        most: The most points kept.

    Returns:
        The kept points in order; all points if the line has no more than `most`.
    """
    if len(points) <= most:
        return points
    # Evenly spread indices rounded to whole points; unique drops an index that rounding repeats
    return points[np.unique(np.linspace(0, len(points) - 1, most).round().astype(int))]


def heliographic_position(west: np.ndarray, north: np.ndarray, b0: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Finds the heliographic position of points on the visible solar disk, the inverse of disk_position.

    Args:
        west: The position to the west as share of the solar radius from the disk centre.
        north: The position to the north as share of the solar radius from the disk centre.
        b0: The tilt of the solar axis towards Earth in degrees.

    Returns:
        The latitude and the longitude from the central meridian in degrees, west positive.
    """
    tilt = np.radians(b0)
    # Distance of the point towards Earth from the plane of the sky through the solar centre
    towards = np.sqrt(np.clip(1 - west**2 - north**2, 0, None))
    latitude = np.arcsin(np.clip(north * np.cos(tilt) + towards * np.sin(tilt), -1, 1))
    longitude = np.arctan2(west, towards * np.cos(tilt) - north * np.sin(tilt))
    return np.degrees(latitude), np.degrees(longitude)


# Location of a sunspot region in the reports of NOAA (US National Oceanic and Atmospheric Administration), such as S15W53:
# group 1 the hemisphere N or S, group 2 the latitude of one or two digits, group 3 the side E or W of the central
# meridian, group 4 the longitude of one to three digits; ^ and $ allow nothing before or after
LOCATION_PATTERN = re.compile(r"^([NS])(\d{1,2})([EW])(\d{1,3})$")


def location_coordinates(location: object) -> tuple[float, float] | None:
    """Reads a NOAA region location such as S15W53 as heliographic latitude and longitude, north and west positive.

    Args:
        location: The location text of the report, or the missing value of a table column.

    Returns:
        The latitude and the longitude from the central meridian in degrees; None for a value that is no text of this form.
    """
    match = LOCATION_PATTERN.match(location.strip()) if isinstance(location, str) else None
    if match is None:
        return None
    # South and east count negative
    latitude = float(match.group(2)) * (1 if match.group(1) == "N" else -1)
    longitude = float(match.group(4)) * (1 if match.group(3) == "W" else -1)
    return latitude, longitude
