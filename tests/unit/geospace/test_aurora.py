from datetime import UTC, datetime

import numpy as np
import pytest

from space_weather_analysis.geospace.aurora import DIPOLE_POLE_LATITUDE, DIPOLE_POLE_LONGITUDE, geomagnetic_latitude, night_side_edge, subsolar_point


def test_dipole_pole_lies_at_ninety_degrees_and_the_opposite_point_at_minus_ninety() -> None:
    """The pole of the dipole has geomagnetic latitude 90 degrees, its antipode -90 degrees."""
    latitudes = geomagnetic_latitude(
        np.array([DIPOLE_POLE_LATITUDE, -DIPOLE_POLE_LATITUDE]), np.array([DIPOLE_POLE_LONGITUDE, DIPOLE_POLE_LONGITUDE + 180])
    )

    assert latitudes == pytest.approx([90.0, -90.0])


def test_sun_stands_over_the_tropic_of_capricorn_at_the_december_solstice() -> None:
    """Near noon UTC of the December solstice the Sun stands overhead near 23.4 degrees south and close to Greenwich."""
    latitude, longitude = subsolar_point(datetime(2026, 12, 21, 12, tzinfo=UTC))

    assert (latitude, longitude) == (pytest.approx(-23.4, abs=0.1), pytest.approx(0.0, abs=1.5))


def test_night_side_edge_takes_the_cell_closest_to_the_equator_per_longitude() -> None:
    """Of two aurora cells on one night-side longitude the one closer to the equator sets the edge; day-side cells count not."""
    latitude = np.array([70.0, 60.0, 50.0])
    # Midnight lies opposite the subsolar longitude 0, the day-side cell at longitude 0 reaches furthest south
    longitude = np.array([180.0, 180.0, 0.0])
    probability = np.array([40.0, 15.0, 50.0])

    edge = night_side_edge(latitude, longitude, probability, 0.0, 10.0, north=True)

    assert edge == pytest.approx(float(geomagnetic_latitude(np.array([60.0]), np.array([180.0]))[0]))
