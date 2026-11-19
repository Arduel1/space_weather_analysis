import numpy as np
import pandas as pd
import pytest

from pipeline_service.parsing.hapi import parse_hapi_times


def test_calendar_and_day_of_year_times_cut_after_any_field_read_as_utc() -> None:
    """Both HAPI date forms read as the same UTC times, whether given in full, cut off early or as bytes."""
    values = np.array(
        [b"2024-03-01T12:30:15.250Z", b"2024-061T12:30:15.250Z", b"2024-03-01T12:30Z", b"2024-061T12Z", b"2024-03-01Z", b"2024-061Z", b"2024Z"]
    )

    times = parse_hapi_times(values)

    expected = ["2024-03-01 12:30:15.25", "2024-03-01 12:30:15.25", "2024-03-01 12:30", "2024-03-01 12:00", "2024-03-01", "2024-03-01", "2024-01-01"]
    assert times.equals(pd.DatetimeIndex(expected, tz="UTC").as_unit(times.unit))


def test_value_that_is_no_hapi_time_is_rejected() -> None:
    """A value that is no time raises ValueError."""
    with pytest.raises(ValueError):
        parse_hapi_times(np.array(["2024-03-01T00:00:00Z", "fill"]))
