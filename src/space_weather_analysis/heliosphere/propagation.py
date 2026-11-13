import math
from datetime import timedelta

import astropy.units as u
import numpy as np

# One astronomical unit in kilometres (IAU 2012 resolution B2)
AU_KM = u.au.to(u.km)
# The nominal solar radius in kilometres (IAU 2015 resolution B3)
SOLAR_RADIUS_KM = u.R_sun.to(u.km)
# Distance from which DONKI (the space weather database of NASA's Community Coordinated Modeling Center) gives the time
# of the front of a coronal mass ejection (CME), and from which the drag of the solar wind rules its flight
START_DISTANCE_KM = 21.5 * SOLAR_RADIUS_KM
# Drag parameter of the drag-based model in 1/km, which treats a CME as a body slowed or sped up by the drag of the solar
# wind: the lower end of the range 0.2e-7 to 2e-7 /km that Vrsnak et al.
# (2013), Solar Physics 285, 295, doi:10.1007/s11207-012-0035-4, found, suited to the fast CMEs that reach the planets
DRAG_PARAMETER = 0.2e-7
# Rotation of the Sun seen from the stars in radians per second, from the sidereal period of 25.38 days that Carrington
# determined (Thompson 2006, Astronomy & Astrophysics 449, 791, doi:10.1051/0004-6361:20054262)
SOLAR_ROTATION = math.tau / timedelta(days=25.38).total_seconds()


def drag_based_distance(seconds: np.ndarray, speed: float, wind_speed: float, drag: float = DRAG_PARAMETER) -> np.ndarray:
    """Computes the distance of a CME front from the Sun after its time at START_DISTANCE_KM with the drag-based model.

    The solar wind drags a CME towards its own speed: a faster CME slows down, a slower one speeds up (Vrsnak et al.
    2013, equation 2): r(t) = s/drag ln(1 + s drag (v0 - w) t) + w t + r0, with s the sign of v0 - w.

    Args:
        seconds: The times after the start in seconds, not negative.
        speed: The speed v0 of the CME at the start in km/s.
        wind_speed: The speed w of the solar wind around it in km/s.
        drag: The drag parameter in 1/km.

    Returns:
        The distances from the centre of the Sun in astronomical units.
    """
    excess = speed - wind_speed
    # The sign picks the branch of the solution for a CME faster or slower than the wind
    sign = 1.0 if excess >= 0 else -1.0
    distance_km = sign / drag * np.log1p(sign * drag * excess * seconds) + wind_speed * seconds + START_DISTANCE_KM
    return distance_km / AU_KM


def ecliptic_half_width(latitude: float, half_angle: float) -> float | None:
    """Computes half the width in longitude of the cut of a CME cone with the plane of the planets.

    A cone of the given half angle around a direction at this heliographic latitude meets the plane of the solar equator,
    close to that of the planets, over the longitudes whose angle to the axis stays within the half angle:
    cos(width) = cos(half angle) / cos(latitude).

    Args:
        latitude: The latitude of the cone axis in degrees.
        half_angle: Half the opening angle of the cone in degrees.

    Returns:
        The half width in degrees; None if the cone passes above or below the plane.
    """
    ratio = math.cos(math.radians(half_angle)) / math.cos(math.radians(latitude))
    if ratio > 1:
        return None
    return math.degrees(math.acos(ratio))


def spiral_longitudes(
    distances_au: np.ndarray, longitude: float | np.ndarray, distance_au: float | np.ndarray, wind_speed: float | np.ndarray
) -> np.ndarray:
    """Computes the longitudes of the Parker spiral through a place at the given distances from the Sun.

    The solar wind leaves the Sun radially while the Sun turns beneath it, so the magnetic field it carries winds into an
    Archimedean spiral (Parker 1958, Astrophysical Journal 128, 664, doi:10.1086/146579): towards the Sun the line
    reaches longitudes further west by the solar rotation times the travel time of the wind.

    Args:
        distances_au: The distances from the Sun in astronomical units.
        longitude: The heliographic longitude of the place in degrees, west positive; one or one per distance.
        distance_au: The distance of the place from the Sun in astronomical units; one or one per distance.
        wind_speed: The speed of the solar wind in km/s; one or one per distance.

    Returns:
        The longitudes in degrees, one per distance.
    """
    # The time the wind needs from each distance out to the place, during which the Sun turns on
    travel_seconds = (distance_au - distances_au) * AU_KM / wind_speed
    return longitude + np.degrees(SOLAR_ROTATION * travel_seconds)
