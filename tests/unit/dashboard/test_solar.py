import numpy as np
import pandas as pd
import pytest

from space_weather_analysis.dashboard.panels.sun import magnetic_class_name
from space_weather_analysis.solar.geometry import disk_position, location_coordinates, solar_b0, synodic_rotation


def test_the_solar_axis_tilts_furthest_towards_earth_in_september_and_away_in_march() -> None:
    """B0 reaches about +7.25 degrees early in September, about -7.25 early in March, and about 0 early in June and December."""
    times = pd.DatetimeIndex(["2024-09-08", "2024-03-06", "2024-06-06", "2024-12-08"], tz="UTC")

    tilt = solar_b0(times)

    assert np.round(tilt).tolist() == [7.0, -7.0, 0.0, 0.0]


def test_disk_positions_follow_latitude_longitude_and_the_tilt() -> None:
    """The disk centre lies at the origin, the west limb 90 degrees west, a point behind the Sun is hidden, a tilt moves the equator south."""
    west, north, visible = disk_position(np.array([0.0, 0.0, 0.0, 0.0]), np.array([0.0, 90.0, 120.0, 0.0]), np.array([0.0, 0.0, 0.0, 7.0]))

    assert (np.round(west, 3).tolist(), np.round(north, 3).tolist(), visible.tolist()) == (
        [0.0, 1.0, 0.866, 0.0],
        [0.0, 0.0, 0.0, -0.122],
        [True, True, False, True],
    )


def test_sunspots_cross_the_disk_slower_at_high_latitude() -> None:
    """Seen from Earth the equator turns by about 13.7 degrees per day and a latitude of 30 degrees more slowly."""
    rates = synodic_rotation(np.array([0.0, 30.0]))

    assert (round(float(rates[0]), 2), bool(rates[1] < rates[0])) == (13.73, True)


@pytest.mark.parametrize(("location", "expected"), [("S15W53", (-15.0, 53.0)), ("N05E10", (5.0, -10.0)), ("S15", None)])
def test_region_locations_read_north_and_west_as_positive(location: str, expected: tuple[float, float] | None) -> None:
    """A NOAA location gives latitude and longitude with north and west positive; another text gives nothing."""
    assert location_coordinates(location) == expected


def test_magnetic_class_codes_are_spelled_out() -> None:
    """Each letter of a Mount Wilson code becomes its Greek name; a missing code counts as not classified."""
    assert [magnetic_class_name(code) for code in ("A", "BGD", None)] == ["alpha", "beta-gamma-delta", "not classified"]
