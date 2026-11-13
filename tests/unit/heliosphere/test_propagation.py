import numpy as np
import pytest

from space_weather_analysis.heliosphere.propagation import AU_KM, START_DISTANCE_KM, drag_based_distance, ecliptic_half_width, spiral_longitudes

DAY_SECONDS = 86400.0


@pytest.mark.parametrize(("speed", "slower"), [pytest.param(1500.0, True, id="fast-cme-slows"), pytest.param(250.0, False, id="slow-cme-speeds-up")])
def test_drag_pulls_a_cme_towards_the_speed_of_the_wind(speed: float, slower: bool) -> None:
    """A CME starts at 21.5 solar radii and its mean speed over later days moves towards the wind speed of 400 km/s."""
    distances = drag_based_distance(np.array([0.0, DAY_SECONDS, 2 * DAY_SECONDS]), speed, 400.0)
    first_day, second_day = np.diff(distances) * AU_KM / DAY_SECONDS

    assert (distances[0] * AU_KM, second_day < first_day if slower else second_day > first_day) == (pytest.approx(START_DISTANCE_KM), True)


def test_cme_at_wind_speed_flies_straight_on() -> None:
    """Without difference to the wind the front keeps its speed: 400 km/s for a day adds 34.56 million km."""
    distances = drag_based_distance(np.array([0.0, DAY_SECONDS]), 400.0, 400.0)

    assert (distances[1] - distances[0]) * AU_KM == pytest.approx(400.0 * DAY_SECONDS)


@pytest.mark.parametrize(
    ("latitude", "half_angle", "expected"),
    [pytest.param(0.0, 30.0, 30.0, id="in-plane"), pytest.param(40.0, 30.0, None, id="above-the-plane"), pytest.param(20.0, 40.0, 35.4, id="tilted")],
)
def test_cone_cuts_the_plane_of_the_planets(latitude: float, half_angle: float, expected: float | None) -> None:
    """A cone in the plane keeps its half angle, a cone above it misses the plane, a tilted one cuts a narrower width."""
    width = ecliptic_half_width(latitude, half_angle)

    assert (round(width, 1) if width is not None else None) == expected


def test_field_line_through_earth_meets_the_sun_about_sixty_degrees_west() -> None:
    """At 400 km/s the spiral through Earth reaches the solar surface about 61 degrees west of the central meridian."""
    # 0.00465 AU is the solar radius
    footpoint = spiral_longitudes(np.array([0.00465]), 0.0, 1.0, 400.0)

    assert footpoint[0] == pytest.approx(61.0, abs=1.0)
