from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import astropy.units as u
import pandas as pd
import pytest
from astropy.coordinates import SkyCoord
from astropy.time import Time
from sunpy.coordinates import frames

from space_weather_analysis.clients.ephemeris import Body, body_positions, daily_times, load_bodies, parse_vectors
from space_weather_analysis.util.error_handling import DataProcessingError

TIMES = daily_times(datetime(2026, 11, 1, 6, tzinfo=UTC), datetime(2026, 11, 3, 18, tzinfo=UTC))


def _fake_locator(body: Body, times: Sequence[datetime]) -> SkyCoord:
    """Places every body at a fixed longitude growing by one degree a day; the ID -999 is unknown."""
    if body.ephemeris_id == "-999":
        raise ValueError("No ephemeris for target -999")
    days = range(len(times))
    return SkyCoord(
        lon=[190.0 + day for day in days] * u.deg,
        lat=[1.0] * len(times) * u.deg,
        radius=[0.9] * len(times) * u.AU,
        frame=frames.HeliographicStonyhurst,
        obstime=Time(list(times)),
    )


def test_positions_of_each_body_per_day_leave_out_a_failing_body() -> None:
    """Each body gets one row per midnight with longitude wrapped to +-180 degrees; a body Horizons does not know is reported."""
    bodies = {"Probe": Body(kind="spacecraft", ephemeris_id="-1"), "Lost": Body(kind="spacecraft", ephemeris_id="-999")}

    positions, failed = body_positions(bodies, TIMES, _fake_locator)

    assert (list(positions.index), positions["longitude"].round(6).tolist(), positions["body"].unique().tolist(), failed) == (
        list(pd.DatetimeIndex(["2026-11-01", "2026-11-02", "2026-11-03"], tz="UTC")),
        [-170.0, -169.0, -168.0],
        ["Probe"],
        ["Lost"],
    )


def test_bodies_file_of_the_project_is_valid() -> None:
    """The bodies file the pipeline service reads loads and lists Earth with the planets."""
    bodies = load_bodies(Path(__file__).parents[3] / "config" / "sources" / "bodies.toml")

    assert (bodies["Earth"].kind, bodies["STEREO A"].ephemeris_id) == ("planet", "-234")


def test_horizons_text_table_gives_one_position_per_time() -> None:
    """The positions between the table markers are read in order; a table with another number of positions is rejected."""
    text = (
        "Reference frame : Ecliptic of J2000.0\n$$SOE\n"
        "2461324.500000000 = A.D. 2026-Oct-11 00:00:00.0000 TDB\n X = 3.27E-02 Y = 9.66E-01 Z =-1.78E-03\n"
        "2461325.500000000 = A.D. 2026-Oct-12 00:00:00.0000 TDB\n X = 1.53E-02 Y = 9.67E-01 Z =-1.81E-03\n$$EOE\n"
    )

    vectors = parse_vectors(text, 2)

    assert vectors.tolist() == [[0.0327, 0.966, -0.00178], [0.0153, 0.967, -0.00181]]
    with pytest.raises(DataProcessingError):
        parse_vectors(text, 3)
