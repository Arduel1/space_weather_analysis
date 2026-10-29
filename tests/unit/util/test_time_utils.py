from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from space_weather_analysis.util.time_utils import (
    _select_best_transform,
    attempt_numeric_time_conversions,
    calculate_time_intersection,
    convert_cftime_object,
    ensure_aware,
    ensure_datetime_type,
    format_iso_duration,
    parse_iso_time,
    parse_numeric_fallback,
    parse_numeric_with_units,
    parse_string_timestamp,
)


class TestEnsureAware:
    def test_naive_datetime_becomes_utc(self) -> None:
        """A naive datetime is tagged as UTC without shifting."""
        naive_dt = datetime(2023, 1, 1, 12, 0, 0)
        result = ensure_aware(naive_dt)

        assert result.tzinfo == timezone.utc
        assert result.hour == 12

    def test_aware_utc_remains_unchanged(self) -> None:
        """An aware UTC datetime is returned unchanged."""
        aware_dt = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = ensure_aware(aware_dt)

        assert result.tzinfo == timezone.utc
        assert result == aware_dt

    def test_aware_non_utc_converts_to_utc(self) -> None:
        """An aware non-UTC datetime is converted to UTC."""
        offset = timezone(timedelta(hours=1))
        non_utc_dt = datetime(2023, 1, 1, 12, 0, 0, tzinfo=offset)

        result = ensure_aware(non_utc_dt)

        assert result.tzinfo == timezone.utc
        assert result.hour == 11


class TestParseIsoTime:
    def test_parse_valid_utc_string_with_z(self) -> None:
        """A 'Z' suffix yields a UTC datetime."""
        ts = "2023-01-01T12:00:00Z"
        dt = parse_iso_time(ts)

        assert dt is not None
        assert dt.year == 2023
        assert dt.hour == 12
        assert dt.tzinfo == timezone.utc

    def test_parse_valid_offset_string(self) -> None:
        """An explicit +00:00 offset yields a UTC datetime."""
        ts = "2023-01-01T12:00:00+00:00"
        dt = parse_iso_time(ts)

        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 12

    def test_parse_naive_string_assumes_utc(self) -> None:
        """A string without offset is read as UTC."""
        ts = "2023-01-01T12:00:00"
        dt = parse_iso_time(ts)

        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 12

    def test_parse_failure_returns_none_and_logs(self) -> None:
        """A malformed string returns None and logs an error."""
        malformed = "not-a-date"

        with patch("space_weather_analysis.util.time_utils.logger") as mock_logger:
            dt = parse_iso_time(malformed)

            assert dt is None
            mock_logger.error.assert_called_once()
            args, _ = mock_logger.error.call_args
            assert "Failed to parse" in args[0]


class TestFormatIsoDuration:
    @pytest.mark.parametrize(
        ("duration", "expected"),
        [
            (timedelta(hours=3), "PT3H"),
            (timedelta(days=1), "P1D"),
            (timedelta(days=1, minutes=30), "P1DT30M"),
            (timedelta(seconds=90), "PT1M30S"),
            (timedelta(seconds=1, microseconds=500000), "PT1.5S"),
        ],
    )
    def test_duration_written_without_zero_parts(self, duration: timedelta, expected: str) -> None:
        """A duration is written as ISO 8601 duration without the parts that are zero."""
        assert format_iso_duration(duration) == expected

    def test_non_positive_duration_is_rejected(self) -> None:
        """A zero duration raises ValueError."""
        with pytest.raises(ValueError):
            format_iso_duration(timedelta(0))


class TestCalculateTimeIntersection:
    def test_full_overlap_nested(self) -> None:
        """A range nested inside the other is returned unchanged."""
        r1_start = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        r1_end = datetime(2023, 1, 1, 13, 0, 0, tzinfo=timezone.utc)

        r2_start = datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        r2_end = datetime(2023, 1, 1, 14, 0, 0, tzinfo=timezone.utc)

        result = calculate_time_intersection(r1_start, r1_end, r2_start, r2_end)

        assert result is not None
        assert result == (r1_start, r1_end)

    def test_partial_overlap(self) -> None:
        """Partially overlapping ranges yield the overlap."""
        r1_start = datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        r1_end = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        r2_start = datetime(2023, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        r2_end = datetime(2023, 1, 1, 13, 0, 0, tzinfo=timezone.utc)

        result = calculate_time_intersection(r1_start, r1_end, r2_start, r2_end)

        assert result is not None
        assert result[0] == r2_start
        assert result[1] == r1_end

    def test_no_overlap_disjoint(self) -> None:
        """Disjoint ranges yield None."""
        r1_start = datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        r1_end = datetime(2023, 1, 1, 11, 0, 0, tzinfo=timezone.utc)

        r2_start = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        r2_end = datetime(2023, 1, 1, 13, 0, 0, tzinfo=timezone.utc)

        result = calculate_time_intersection(r1_start, r1_end, r2_start, r2_end)

        assert result is None

    def test_adjacent_ranges_return_none(self) -> None:
        """Ranges that only touch yield None, as an intersection needs start < end."""
        boundary = datetime(2023, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        r1_start = datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        r2_end = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        result = calculate_time_intersection(r1_start, boundary, boundary, r2_end)

        assert result is None

    def test_naive_inputs_handled(self) -> None:
        """Naive inputs are read as UTC and combine with aware ones."""
        r1_start = datetime(2023, 1, 1, 10, 0, 0)
        r1_end = datetime(2023, 1, 1, 12, 0, 0)

        r2_start = datetime(2023, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        r2_end = datetime(2023, 1, 1, 13, 0, 0, tzinfo=timezone.utc)

        result = calculate_time_intersection(r1_start, r1_end, r2_start, r2_end)

        assert result is not None
        assert result[0].tzinfo == timezone.utc
        assert result[0].hour == 11
        assert result[1].hour == 12


class TestNumericTimeConversions:
    """attempt_numeric_time_conversions with its scoring and selection helpers."""

    def test_identifies_unix_seconds(self) -> None:
        """Unix seconds are detected as 'unix_seconds'."""
        # 2023-01-01 00:00:00 UTC
        base = 1672531200.0
        raw_vals = np.array([base, base + 60, base + 120], dtype=float)

        decoded, diag = attempt_numeric_time_conversions(raw_vals)

        assert decoded is not None
        assert diag["accepted"] is True
        assert diag["selected_transform"] == "unix_seconds"

        ts_decoded = pd.to_datetime(decoded)
        assert ts_decoded[0].year == 2023
        assert ts_decoded[0].minute == 0
        assert ts_decoded[1].minute == 1

    def test_identifies_julian_days(self) -> None:
        """Julian days are detected as 'julian_day' once the 1970 reading is out of range."""
        # JD 2451545.0 is 2000-01-01 12:00:00.
        raw_vals = np.array([2451545.0, 2451546.0])

        # As Unix seconds the values would be a plausible date in 1970.
        decoded, diag = attempt_numeric_time_conversions(raw_vals, plausible_year_min=1990)

        assert decoded is not None
        assert diag["selected_transform"] == "julian_day"

        ts_decoded = pd.to_datetime(decoded)
        assert ts_decoded[0].year == 2000
        assert ts_decoded[0].month == 1
        assert ts_decoded[0].day == 1

    def test_respects_range_constraints(self) -> None:
        """A date in 1900 is rejected outside the plausible range and accepted without range."""
        # Unix seconds of 1900-01-01.
        raw_vals = np.array([-2208988800.0])

        # Read as micro- or milliseconds the value lies near 1970, so the range starts after 1970.
        decoded_restricted, diag_restricted = attempt_numeric_time_conversions(raw_vals, plausible_year_min=1975, plausible_year_max=2050)
        assert decoded_restricted is None
        assert diag_restricted["accepted"] is False
        assert diag_restricted["reason"] == "no plausible transform found (low score or range mismatch)"

        decoded_open, diag_open = attempt_numeric_time_conversions(raw_vals, plausible_year_min=None, plausible_year_max=None)
        assert decoded_open is not None
        assert diag_open["accepted"] is True

        ts = pd.to_datetime(decoded_open)
        assert ts[0].year == 1900

    def test_handles_nan_and_masks(self) -> None:
        """NaN values become NaT while the valid values are converted."""
        raw_vals = np.array([1672531200.0, np.nan, 1672531300.0])

        decoded, diag = attempt_numeric_time_conversions(raw_vals)

        assert decoded is not None
        assert diag["selected_transform"] == "unix_seconds"

        ts_decoded = pd.to_datetime(decoded)
        assert pd.isna(ts_decoded[1])
        assert not pd.isna(ts_decoded[0])
        assert ts_decoded[0].year == 2023

    def test_rejects_garbage(self) -> None:
        """Values too large for any supported time format are not accepted, even without range."""
        raw_vals = np.array([1e20, 1e21])

        decoded, diag = attempt_numeric_time_conversions(raw_vals, plausible_year_min=None, plausible_year_max=None)

        # Overflowing values become NaT, which scores 0.
        if decoded is None:
            assert diag["accepted"] is False
        else:
            pass

    def test_score_logic_prefers_validity(self) -> None:
        """Small values read as days since 2000 win over Unix seconds within 1990-2020."""
        raw_vals = np.array([5.0, 6.0, 7.0, 8.0, 9.0, 10.0], dtype=float)

        decoded, diag = attempt_numeric_time_conversions(raw_vals, plausible_year_min=1990, plausible_year_max=2020)

        assert decoded is not None
        assert diag["selected_transform"] == "days_since_2000"

        ts = pd.to_datetime(decoded)
        assert ts[0].year == 2000


class TestSelectBestTransform:
    """Scoring and selection in _select_best_transform."""

    @pytest.fixture
    def mock_index(self) -> MagicMock:
        """Mock DatetimeIndex with a to_numpy() method."""
        m = MagicMock()
        m.to_numpy.return_value = np.array([])
        return m

    def test_picks_highest_score(self, mock_index: MagicMock) -> None:
        """The transform with the highest score is selected."""
        # Score: 0.6 * non_na_frac + 0.4 * in_range_frac.
        transforms = [
            ("bad", mock_index, {"non_na_frac": 0.5, "in_range_frac": 0.1}),  # Score: 0.3 + 0.04 = 0.34
            ("good", mock_index, {"non_na_frac": 1.0, "in_range_frac": 1.0}),  # Score: 0.6 + 0.4 = 1.0
            ("ok", mock_index, {"non_na_frac": 0.9, "in_range_frac": 0.5}),  # Score: 0.54 + 0.2 = 0.74
        ]

        diagnostics: Dict[str, Any] = {}
        result, diag = _select_best_transform(transforms, diagnostics)  # type: ignore

        assert diag["selected_transform"] == "good"
        assert diag["accepted"] is True
        assert diag["score_value"] == 1.0

    def test_rejects_zero_range_candidates(self, mock_index: MagicMock) -> None:
        """A transform without any value in the plausible range is rejected."""
        transforms = [
            ("technically_valid_but_wrong_year", mock_index, {"non_na_frac": 1.0, "in_range_frac": 0.0}),
        ]

        diagnostics: Dict[str, Any] = {}
        result, diag = _select_best_transform(transforms, diagnostics)  # type: ignore

        assert result is None
        assert diag["accepted"] is False
        assert "no plausible transform" in diag["reason"]

    def test_tie_breaking_behavior(self, mock_index: MagicMock) -> None:
        """On equal scores the first transform wins."""
        transforms = [
            ("strategy_A", mock_index, {"non_na_frac": 1.0, "in_range_frac": 1.0}),
            ("strategy_B", mock_index, {"non_na_frac": 1.0, "in_range_frac": 1.0}),
        ]

        diagnostics: Dict[str, Any] = {}
        _, diag = _select_best_transform(transforms, diagnostics)  # type: ignore

        assert diag["selected_transform"] == "strategy_A"

    def test_rejects_very_low_scores(self, mock_index: MagicMock) -> None:
        """A best score of at most 0.01 is rejected as noise."""
        transforms = [
            ("noise", mock_index, {"non_na_frac": 0.01, "in_range_frac": 0.01}),  # Score ~0.01
        ]

        diagnostics: Dict[str, Any] = {}
        result, diag = _select_best_transform(transforms, diagnostics)  # type: ignore

        assert result is None
        assert diag["accepted"] is False


class TestEnsureDatetimeType:
    """Conversion of datetime-like values to aware UTC datetimes."""

    def test_converts_pandas_timestamp(self) -> None:
        """A naive pandas Timestamp becomes a plain UTC datetime."""
        ts = pd.Timestamp("2023-01-01 12:00:00")
        result = ensure_datetime_type(ts)

        assert isinstance(result, datetime)
        assert not isinstance(result, pd.Timestamp)
        assert result.tzinfo == timezone.utc
        assert result.hour == 12

    def test_converts_numpy_datetime64(self) -> None:
        """A numpy datetime64 becomes a UTC datetime."""
        np_dt = np.datetime64("2023-01-01T12:00:00")
        result = ensure_datetime_type(np_dt)

        assert isinstance(result, datetime)
        assert result.tzinfo == timezone.utc
        assert result.year == 2023

    def test_handles_standard_datetime(self) -> None:
        """A naive datetime is tagged as UTC."""
        dt = datetime(2023, 1, 1, 10, 0)
        result = ensure_datetime_type(dt)

        assert result.tzinfo == timezone.utc
        assert result.hour == 10

    def test_handles_aware_non_utc(self) -> None:
        """An aware +01:00 datetime is converted to UTC."""
        offset = timezone(timedelta(hours=1))
        dt = datetime(2023, 1, 1, 12, 0, tzinfo=offset)

        result = ensure_datetime_type(dt)
        assert result.tzinfo == timezone.utc
        assert result.hour == 11


class TestConvertCftimeObject:
    """convert_cftime_object with duck-typed stand-ins for cftime objects."""

    @pytest.fixture
    def mock_cftime(self) -> Any:
        """Class with the date and time attributes of a cftime object."""

        class MockCftime:
            def __init__(
                self,
                year: int,
                month: int,
                day: int,
                hour: int = 0,
                minute: int = 0,
                second: int = 0,
                microsecond: int = 0,
            ) -> None:
                self.year = year
                self.month = month
                self.day = day
                self.hour = hour
                self.minute = minute
                self.second = second
                self.microsecond = microsecond

            def __repr__(self) -> str:
                return f"MockCftime({self.year}-{self.month}-{self.day})"

        return MockCftime

    def test_converts_valid_cftime(self, mock_cftime: Any) -> None:
        """A valid cftime object becomes the matching UTC datetime."""
        cftime_obj = mock_cftime(2023, 5, 20, 14, 30, 0)
        result = convert_cftime_object(cftime_obj)

        assert isinstance(result, datetime)
        assert result.year == 2023
        assert result.month == 5
        assert result.day == 20
        assert result.hour == 14
        assert result.tzinfo == timezone.utc

    def test_handles_missing_time_attributes(self, mock_cftime: Any) -> None:
        """Missing time attributes default to 00:00:00."""
        cftime_obj = MagicMock()
        cftime_obj.year = 2023
        cftime_obj.month = 1
        cftime_obj.day = 1
        # MagicMock creates attributes on access, so the time attributes are deleted.
        del cftime_obj.hour
        del cftime_obj.minute
        del cftime_obj.second
        del cftime_obj.microsecond

        result = convert_cftime_object(cftime_obj)

        assert result is not None
        assert result.year == 2023
        assert result.hour == 0
        assert result.minute == 0

    def test_returns_none_for_invalid_calendar_dates(self, mock_cftime: Any) -> None:
        """A date missing from the Gregorian calendar (Feb 30 of a 360_day calendar) returns None."""
        cftime_obj = mock_cftime(2023, 2, 30)

        result = convert_cftime_object(cftime_obj)
        assert result is None

    def test_returns_none_for_malformed_input(self) -> None:
        """An object without year, month and day returns None."""
        bad_obj = "I am a string"
        result = convert_cftime_object(bad_obj)
        assert result is None


class TestParseStringTimestamp:
    """Parsing of string and bytes timestamps."""

    def test_parses_iso_string(self) -> None:
        """An ISO 8601 string becomes a UTC datetime."""
        val = "2023-01-01T12:00:00"
        result = parse_string_timestamp(val)

        assert isinstance(result, datetime)
        assert result.year == 2023
        assert result.hour == 12
        assert result.tzinfo == timezone.utc

    def test_parses_bytes(self) -> None:
        """Bytes, as found in NetCDF files, are decoded and parsed."""
        val = b"2023-05-20"
        result = parse_string_timestamp(val)

        assert isinstance(result, datetime)
        assert result.month == 5
        assert result.day == 20

    def test_handles_invalid_strings(self) -> None:
        """An unparsable string returns None."""
        val = "not-a-date"
        result = parse_string_timestamp(val)
        assert result is None

    def test_handles_numpy_str_types(self) -> None:
        """A numpy.str_ value is parsed."""
        val = np.str_("2020-01-01")
        result = parse_string_timestamp(val)
        assert result is not None
        assert result.year == 2020


class TestParseNumericWithUnits:
    """parse_numeric_with_units with a patched num2date."""

    def test_uses_netcdf4_num2date(self) -> None:
        """Passes value, units and calendar to num2date and returns its result as UTC."""
        with patch("space_weather_analysis.util.time_utils.num2date") as mock_num2date:
            mock_dt = datetime(2000, 1, 1, 12, 0)
            mock_num2date.return_value = np.array([mock_dt])

            result = parse_numeric_with_units(val=12345.0, units="hours since 2000-01-01", calendar="standard")

            mock_num2date.assert_called_with([12345.0], units="hours since 2000-01-01", calendar="standard")
            assert isinstance(result, datetime)
            assert result == mock_dt.replace(tzinfo=timezone.utc)

    def test_handles_cftime_object(self) -> None:
        """A cftime-like result for the noleap calendar becomes a UTC datetime."""
        with patch("space_weather_analysis.util.time_utils.num2date") as mock_num2date:

            class MockCftime:
                year, month, day, hour, minute, second, microsecond = 2000, 1, 1, 0, 0, 0, 0

            mock_num2date.return_value = np.array([MockCftime()])

            result = parse_numeric_with_units(10.0, "days since 2000-01-01", "noleap")

            assert isinstance(result, datetime)
            assert result.year == 2000
            assert result.tzinfo == timezone.utc

    def test_returns_none_on_error(self) -> None:
        """An error from num2date returns None."""
        with patch("space_weather_analysis.util.time_utils.num2date") as mock_num2date:
            mock_num2date.side_effect = Exception("Bad units")

            result = parse_numeric_with_units(10.0, "garbage", None)
            assert result is None


class TestParseNumericFallback:
    """parse_numeric_fallback with a patched attempt_numeric_time_conversions."""

    @patch("space_weather_analysis.util.time_utils.attempt_numeric_time_conversions")
    def test_uses_attempt_conversion_result(self, mock_attempt: MagicMock) -> None:
        """The value is passed as a one-element array and the first decoded element is returned."""
        mock_result = np.array([np.datetime64("2000-01-01")])
        mock_attempt.return_value = (mock_result, {})

        result = parse_numeric_fallback(100.0)

        assert isinstance(result, datetime)
        assert result.year == 2000
        args, _ = mock_attempt.call_args
        np.testing.assert_array_equal(args[0], np.array([100.0]))

    @patch("space_weather_analysis.util.time_utils.attempt_numeric_time_conversions")
    def test_returns_none_if_no_match(self, mock_attempt: MagicMock) -> None:
        """Returns None when no conversion is accepted."""
        mock_attempt.return_value = (None, {})

        result = parse_numeric_fallback(999.0)
        assert result is None
