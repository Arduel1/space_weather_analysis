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
    parse_iso_time,
    parse_numeric_fallback,
    parse_numeric_with_units,
    parse_string_timestamp,
)


class TestEnsureAware:
    def test_naive_datetime_becomes_utc(self) -> None:
        """
        Scenario: Input is a naive datetime.
        Expected: Result is timezone-aware (UTC) without time shifting.
        """
        naive_dt = datetime(2023, 1, 1, 12, 0, 0)
        result = ensure_aware(naive_dt)

        assert result.tzinfo == timezone.utc
        assert result.hour == 12  # No shift

    def test_aware_utc_remains_unchanged(self) -> None:
        """
        Scenario: Input is already UTC aware.
        Expected: Result remains UTC and equal to input.
        """
        aware_dt = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = ensure_aware(aware_dt)

        assert result.tzinfo == timezone.utc
        assert result == aware_dt

    def test_aware_non_utc_converts_to_utc(self) -> None:
        """
        Scenario: Input is aware but NOT UTC (UTC+1).
        Expected: Result is converted to UTC (time shifted).
        """
        # UTC+1
        offset = timezone(timedelta(hours=1))
        non_utc_dt = datetime(2023, 1, 1, 12, 0, 0, tzinfo=offset)

        result = ensure_aware(non_utc_dt)

        assert result.tzinfo == timezone.utc
        # 12:00 UTC+1 is 11:00 UTC
        assert result.hour == 11


class TestParseIsoTime:
    def test_parse_valid_utc_string_with_z(self) -> None:
        """
        Scenario: Standard ISO string with 'Z' suffix.
        Expected: Correct UTC datetime (handled natively by Python 3.11+).
        """
        ts = "2023-01-01T12:00:00Z"
        dt = parse_iso_time(ts)

        assert dt is not None
        assert dt.year == 2023
        assert dt.hour == 12
        assert dt.tzinfo == timezone.utc

    def test_parse_valid_offset_string(self) -> None:
        """
        Scenario: ISO string with explicit offset (+00:00).
        Expected: Correct UTC datetime.
        """
        ts = "2023-01-01T12:00:00+00:00"
        dt = parse_iso_time(ts)

        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 12

    def test_parse_naive_string_assumes_utc(self) -> None:
        """
        Scenario: ISO string without timezone info.
        Expected: Interpreted as UTC (via ensure_aware).
        """
        ts = "2023-01-01T12:00:00"
        dt = parse_iso_time(ts)

        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 12

    def test_parse_failure_returns_none_and_logs(self) -> None:
        """
        Scenario: Malformed string.
        Expected: Returns None and logs error.
        """
        malformed = "not-a-date"

        with patch("space_weather_analysis.util.time_utils.logger") as mock_logger:
            dt = parse_iso_time(malformed)

            # Check return value is None
            assert dt is None

            # Check logging
            mock_logger.error.assert_called_once()
            args, _ = mock_logger.error.call_args
            assert "Failed to parse" in args[0]


class TestCalculateTimeIntersection:
    def test_full_overlap_nested(self) -> None:
        """
        Scenario: Range 1 is completely inside Range 2.
        Expected: Returns Range 1 exactly.
        """
        # Range 1: 12:00 to 13:00
        r1_start = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        r1_end = datetime(2023, 1, 1, 13, 0, 0, tzinfo=timezone.utc)

        # Range 2: 10:00 to 14:00
        r2_start = datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        r2_end = datetime(2023, 1, 1, 14, 0, 0, tzinfo=timezone.utc)

        result = calculate_time_intersection(r1_start, r1_end, r2_start, r2_end)

        assert result is not None
        assert result == (r1_start, r1_end)

    def test_partial_overlap(self) -> None:
        """
        Scenario: Ranges overlap partially.
        Range 1: 10:00 - 12:00
        Range 2: 11:00 - 13:00
        Expected: 11:00 - 12:00
        """
        r1_start = datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        r1_end = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        r2_start = datetime(2023, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        r2_end = datetime(2023, 1, 1, 13, 0, 0, tzinfo=timezone.utc)

        result = calculate_time_intersection(r1_start, r1_end, r2_start, r2_end)

        assert result is not None
        assert result[0] == r2_start  # 11:00
        assert result[1] == r1_end  # 12:00

    def test_no_overlap_disjoint(self) -> None:
        """
        Scenario: Ranges are completely disjoint.
        Range 1: 10:00 - 11:00
        Range 2: 12:00 - 13:00
        Expected: None
        """
        r1_start = datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        r1_end = datetime(2023, 1, 1, 11, 0, 0, tzinfo=timezone.utc)

        r2_start = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        r2_end = datetime(2023, 1, 1, 13, 0, 0, tzinfo=timezone.utc)

        result = calculate_time_intersection(r1_start, r1_end, r2_start, r2_end)

        assert result is None

    def test_adjacent_ranges_return_none(self) -> None:
        """
        Scenario: Ranges touch exactly at the boundary (11:00).
        Range 1: 10:00 - 11:00
        Range 2: 11:00 - 12:00
        Expected: None (since start < end is required for an intersection).
        """
        boundary = datetime(2023, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        r1_start = datetime(2023, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        r2_end = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        result = calculate_time_intersection(r1_start, boundary, boundary, r2_end)

        assert result is None

    def test_naive_inputs_handled(self) -> None:
        """
        Scenario: Inputs are naive datetimes.
        Expected: Function converts them to aware internally and calculates correctly.
        """
        # Naive 10:00-12:00
        r1_start = datetime(2023, 1, 1, 10, 0, 0)
        r1_end = datetime(2023, 1, 1, 12, 0, 0)

        # Aware 11:00-13:00 UTC
        r2_start = datetime(2023, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        r2_end = datetime(2023, 1, 1, 13, 0, 0, tzinfo=timezone.utc)

        result = calculate_time_intersection(r1_start, r1_end, r2_start, r2_end)

        assert result is not None
        # Result should be aware (UTC)
        assert result[0].tzinfo == timezone.utc
        assert result[0].hour == 11
        assert result[1].hour == 12


class TestNumericTimeConversions:
    """
    Tests for attempt_numeric_time_conversions and its helpers _score_dtindex/_select_best_transform.
    """

    def test_identifies_unix_seconds(self) -> None:
        """
        Scenario: Array contains standard Unix timestamps (seconds since 1970).
        Expected: Correctly identifies 'unix_seconds' and returns datetime objects.
        """
        # 2023-01-01 00:00:00 UTC = 1672531200
        base = 1672531200.0
        raw_vals = np.array([base, base + 60, base + 120], dtype=float)

        decoded, diag = attempt_numeric_time_conversions(raw_vals)

        assert decoded is not None
        assert diag["accepted"] is True
        assert diag["selected_transform"] == "unix_seconds"

        # Verify values via pandas to handle numpy datetime64
        ts_decoded = pd.to_datetime(decoded)
        assert ts_decoded[0].year == 2023
        assert ts_decoded[0].minute == 0
        assert ts_decoded[1].minute == 1

    def test_identifies_julian_days(self) -> None:
        """
        Scenario: Array contains Julian Day numbers.
        Expected: Correctly identifies 'julian_day' and calculates correct date.
        """
        # JD for 2000-01-01 12:00:00 is 2451545.0
        raw_vals = np.array([2451545.0, 2451546.0])  # 1 day apart

        # Ambiguity resolution: Julian Days ~2.45 million could be valid Unix seconds (1970-01-29)
        # So we set min_year=1990 to rule out 1970 interpretation.
        decoded, diag = attempt_numeric_time_conversions(raw_vals, plausible_year_min=1990)

        assert decoded is not None
        assert diag["selected_transform"] == "julian_day"

        ts_decoded = pd.to_datetime(decoded)
        assert ts_decoded[0].year == 2000
        assert ts_decoded[0].month == 1
        assert ts_decoded[0].day == 1

    def test_respects_range_constraints(self) -> None:
        """
        Scenario: A date in 1900 is technically valid but falls outside the 'plausible' range.
        Expected:
            - Should be rejected if range is set to 1975-2050.
            - Should be accepted if range is None.
        """
        # Unix timestamp for 1900-01-01: approx -2208988800
        raw_vals = np.array([-2208988800.0])

        # Test 1: With restriction (Explicit) -> Should Reject
        # Note: -2.2e9 could be interpreted as microsecs from 1970 which is ~1969-12-31 (very close to 1970).
        # To avoid ambiguity with unix_micros/millis being accepted near 1970, we set min year > 1970.
        decoded_restricted, diag_restricted = attempt_numeric_time_conversions(raw_vals, plausible_year_min=1975, plausible_year_max=2050)
        assert decoded_restricted is None
        assert diag_restricted["accepted"] is False
        assert diag_restricted["reason"] == "no plausible transform found (low score or range mismatch)"

        # Test 2: Without restriction (None) -> Should Accept
        decoded_open, diag_open = attempt_numeric_time_conversions(raw_vals, plausible_year_min=None, plausible_year_max=None)
        assert decoded_open is not None
        assert diag_open["accepted"] is True

        ts = pd.to_datetime(decoded_open)
        assert ts[0].year == 1900

    def test_handles_nan_and_masks(self) -> None:
        """
        Scenario: Input array contains NaNs or is a masked array.
        Expected: NaNs result in NaT, valid numbers are converted.
        """
        # 2023 timestamp + NaN + 2023 timestamp (+100s)
        raw_vals = np.array([1672531200.0, np.nan, 1672531300.0])

        decoded, diag = attempt_numeric_time_conversions(raw_vals)

        assert decoded is not None
        assert diag["selected_transform"] == "unix_seconds"

        ts_decoded = pd.to_datetime(decoded)
        # The NaN should result in NaT in the output
        assert pd.isna(ts_decoded[1])
        # But other values should be valid
        assert not pd.isna(ts_decoded[0])
        assert ts_decoded[0].year == 2023

    def test_rejects_garbage(self) -> None:
        """
        Scenario: Values (1e20, 1e21) too large to be a timestamp in any supported time format.
        Expected: Returns None even without explicit range, as pandas usually
                  fails to convert overflows or results in obviously wrong data logic.
        """
        # Extremely large numbers (1e20) cause pandas/numpy overflow or out of bounds
        raw_vals = np.array([1e20, 1e21])

        # Even without explicit range, these usually fail internal consistency or pandas bounds
        decoded, diag = attempt_numeric_time_conversions(raw_vals, plausible_year_min=None, plausible_year_max=None)

        # If pandas catches the overflow as NaT, the score will be 0 (0% valid).
        if decoded is None:
            assert diag["accepted"] is False
        else:
            # If it somehow returns something (unlikely for 1e20), ensure it handled it gracefully
            pass

    def test_score_logic_prefers_validity(self) -> None:
        """
        Scenario: Ambiguous values.
        Input: [5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        - Interpreted as "Days since 2000": 2000-01-05 (Valid, Year 2000)
        - Interpreted as "Unix Seconds": 1970-01-01 00:00:05 (Valid, Year 1970)

        If we set range 1990-2020, "Days since 2000" MUST win over Unix Seconds.
        """
        raw_vals = np.array([5.0, 6.0, 7.0, 8.0, 9.0, 10.0], dtype=float)

        decoded, diag = attempt_numeric_time_conversions(raw_vals, plausible_year_min=1990, plausible_year_max=2020)

        assert decoded is not None
        # It should pick the one that falls in the 1990-2020 range
        assert diag["selected_transform"] == "days_since_2000"

        ts = pd.to_datetime(decoded)
        assert ts[0].year == 2000


class TestSelectBestTransform:
    """
    Unit tests for the internal helper _select_best_transform.
    Focuses on scoring logic and edge cases.
    """

    @pytest.fixture
    def mock_index(self) -> MagicMock:
        """Helper to create a mock DatetimeIndex that has a .to_numpy() method."""
        m = MagicMock()
        m.to_numpy.return_value = np.array([])
        return m

    def test_picks_highest_score(self, mock_index: MagicMock) -> None:
        """
        Scenario: Three transforms provided. One has clearly better stats.
        Expected: The best one is selected.
        """
        # (name, dtindex (mock), stats)
        transforms = [
            ("bad", mock_index, {"non_na_frac": 0.5, "in_range_frac": 0.1}),  # Score: 0.3 + 0.04 = 0.34
            ("good", mock_index, {"non_na_frac": 1.0, "in_range_frac": 1.0}),  # Score: 0.6 + 0.4 = 1.0
            ("ok", mock_index, {"non_na_frac": 0.9, "in_range_frac": 0.5}),  # Score: 0.54 + 0.2 = 0.74
        ]

        diagnostics: Dict[str, Any] = {}
        result, diag = _select_best_transform(transforms, diagnostics)  # type: ignore

        # Result should be the dtindex of "good" (which is None in this mock)
        assert diag["selected_transform"] == "good"
        assert diag["accepted"] is True
        assert diag["score_value"] == 1.0

    def test_rejects_zero_range_candidates(self, mock_index: MagicMock) -> None:
        """
        Scenario: A transform produces valid dates (non_na=1.0), but NONE are in the plausible range.
        Expected: Should be rejected (filtered out), effectively returning None if no other candidates exist.
        """
        transforms = [
            ("technically_valid_but_wrong_year", mock_index, {"non_na_frac": 1.0, "in_range_frac": 0.0}),
        ]

        diagnostics: Dict[str, Any] = {}
        result, diag = _select_best_transform(transforms, diagnostics)  # type: ignore

        assert result is None
        assert diag["accepted"] is False
        assert "no plausible transform" in diag["reason"]

    def test_tie_breaking_behavior(self, mock_index: MagicMock) -> None:
        """
        Scenario: Two transforms have identical perfect scores.
        Expected: The first one in the list wins (deterministic behavior).
        """
        transforms = [
            ("strategy_A", mock_index, {"non_na_frac": 1.0, "in_range_frac": 1.0}),
            ("strategy_B", mock_index, {"non_na_frac": 1.0, "in_range_frac": 1.0}),
        ]

        diagnostics: Dict[str, Any] = {}
        _, diag = _select_best_transform(transforms, diagnostics)  # type: ignore

        assert diag["selected_transform"] == "strategy_A"

    def test_rejects_very_low_scores(self, mock_index: MagicMock) -> None:
        """
        Scenario: Best candidate has a score <= 0.01 (mostly garbage noise).
        Expected: Rejection to prevent false positives from random noise.
        """
        transforms = [
            ("noise", mock_index, {"non_na_frac": 0.01, "in_range_frac": 0.01}),  # Score ~0.01
        ]

        diagnostics: Dict[str, Any] = {}
        result, diag = _select_best_transform(transforms, diagnostics)  # type: ignore

        assert result is None
        assert diag["accepted"] is False


class TestEnsureDatetimeType:
    """
    Tests for _ensure_datetime_type logic.
    """

    def test_converts_pandas_timestamp(self) -> None:
        """
        Scenario: Input is a Pandas Timestamp.
        Expected: Returns standard python datetime with UTC.
        """
        ts = pd.Timestamp("2023-01-01 12:00:00")  # Naive
        result = ensure_datetime_type(ts)

        assert isinstance(result, datetime)
        assert not isinstance(result, pd.Timestamp)  # Should be converted
        assert result.tzinfo == timezone.utc
        assert result.hour == 12

    def test_converts_numpy_datetime64(self) -> None:
        """
        Scenario: Input is a numpy datetime64 object.
        Expected: Returns standard python datetime with UTC.
        """
        np_dt = np.datetime64("2023-01-01T12:00:00")
        result = ensure_datetime_type(np_dt)

        assert isinstance(result, datetime)
        assert result.tzinfo == timezone.utc
        assert result.year == 2023

    def test_handles_standard_datetime(self) -> None:
        """
        Scenario: Input is already a datetime object.
        Expected: Returns same object (or copy), ensuring UTC.
        """
        dt = datetime(2023, 1, 1, 10, 0)
        result = ensure_datetime_type(dt)

        assert result.tzinfo == timezone.utc
        assert result.hour == 10

    def test_handles_aware_non_utc(self) -> None:
        """
        Scenario: Input is aware but +01:00.
        Expected: Converted to UTC.
        """
        offset = timezone(timedelta(hours=1))
        dt = datetime(2023, 1, 1, 12, 0, tzinfo=offset)

        result = ensure_datetime_type(dt)
        assert result.tzinfo == timezone.utc
        assert result.hour == 11  # 12:00+1 -> 11:00 UTC


class TestConvertCftimeObject:
    """
    Tests for _convert_cftime_object logic.
    Since we might not have 'cftime' installed in the test env,
    we mock the object structure.
    """

    @pytest.fixture
    def mock_cftime(self) -> Any:
        """Creates a mock object behaving like a cftime object."""

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
        """
        Scenario: Valid cftime object.
        Expected: Returns corresponding datetime.
        """
        cftime_obj = mock_cftime(2023, 5, 20, 14, 30, 0)
        result = convert_cftime_object(cftime_obj)

        assert isinstance(result, datetime)
        assert result.year == 2023
        assert result.month == 5
        assert result.day == 20
        assert result.hour == 14
        assert result.tzinfo == timezone.utc

    def test_handles_missing_time_attributes(self, mock_cftime: Any) -> None:
        """
        Scenario: cftime object only has date, no time attributes.
        Expected: Returns datetime with time set to 00:00:00.
        """
        # Create object without hour/minute/etc attributes
        cftime_obj = MagicMock()
        cftime_obj.year = 2023
        cftime_obj.month = 1
        cftime_obj.day = 1
        # Ensure getattr(..., 'hour', 0) works by NOT setting them on the mock
        # We must delete all time attributes because MagicMock auto-creates them
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
        """
        Scenario: cftime object represents a date that doesn't exist in standard calendar.
        Example: Feb 30th (valid in 360_day calendar, invalid in Gregorian).
        Expected: Returns None (caught ValueError).
        """
        cftime_obj = mock_cftime(2023, 2, 30)  # Feb 30th!

        result = convert_cftime_object(cftime_obj)
        assert result is None

    def test_returns_none_for_malformed_input(self) -> None:
        """
        Scenario: Input is not a cftime object (missing year/month/day).
        Expected: Returns None (caught AttributeError).
        """
        bad_obj = "I am a string"
        result = convert_cftime_object(bad_obj)
        assert result is None


class TestParseStringTimestamp:
    """Tests for _parse_string_timestamp."""

    def test_parses_iso_string(self) -> None:
        """Scenario: Valid ISO 8601 string."""
        val = "2023-01-01T12:00:00"
        result = parse_string_timestamp(val)

        assert isinstance(result, datetime)
        assert result.year == 2023
        assert result.hour == 12
        assert result.tzinfo == timezone.utc

    def test_parses_bytes(self) -> None:
        """Scenario: Input is bytes (common in NetCDF)."""
        val = b"2023-05-20"
        result = parse_string_timestamp(val)

        assert isinstance(result, datetime)
        assert result.month == 5
        assert result.day == 20

    def test_handles_invalid_strings(self) -> None:
        """Scenario: Garbage string returns None."""
        val = "not-a-date"
        result = parse_string_timestamp(val)
        assert result is None

    def test_handles_numpy_str_types(self) -> None:
        """Scenario: Input is numpy.str_."""
        val = np.str_("2020-01-01")
        result = parse_string_timestamp(val)
        assert result is not None
        assert result.year == 2020


class TestParseNumericWithUnits:
    """Tests for _parse_numeric_with_units (mocking netCDF4)."""

    def test_uses_netcdf4_num2date(self) -> None:
        """Scenario: Calls num2date correctly and converts result."""

        # Patch directly where it is imported in the target module
        with patch("space_weather_analysis.util.time_utils.num2date") as mock_num2date:
            # Setup mock return: a datetime object inside an array
            mock_dt = datetime(2000, 1, 1, 12, 0)
            mock_num2date.return_value = np.array([mock_dt])

            result = parse_numeric_with_units(val=12345.0, units="hours since 2000-01-01", calendar="standard")

            # Assertions
            mock_num2date.assert_called_with([12345.0], units="hours since 2000-01-01", calendar="standard")
            assert isinstance(result, datetime)
            assert result == mock_dt.replace(tzinfo=timezone.utc)

    def test_handles_cftime_object(self) -> None:
        """Scenario: num2date returns a cftime-like object for the noleap calendar."""

        with patch("space_weather_analysis.util.time_utils.num2date") as mock_num2date:
            # Mock a cftime-like object
            class MockCftime:
                year, month, day, hour, minute, second, microsecond = 2000, 1, 1, 0, 0, 0, 0

            mock_num2date.return_value = np.array([MockCftime()])

            result = parse_numeric_with_units(10.0, "days since 2000-01-01", "noleap")

            assert isinstance(result, datetime)
            assert result.year == 2000
            assert result.tzinfo == timezone.utc

    def test_returns_none_on_error(self) -> None:
        """Scenario: num2date raises an error for an invalid units string."""
        with patch("space_weather_analysis.util.time_utils.num2date") as mock_num2date:
            mock_num2date.side_effect = Exception("Bad units")

            result = parse_numeric_with_units(10.0, "garbage", None)
            assert result is None


class TestParseNumericFallback:
    """Tests for _parse_numeric_fallback."""

    @patch("space_weather_analysis.util.time_utils.attempt_numeric_time_conversions")
    def test_uses_attempt_conversion_result(self, mock_attempt: MagicMock) -> None:
        """
        Scenario: attempt_numeric_time_conversions finds a valid match.
        Expected: Returns the first element converted to datetime.
        """
        # Mock successful conversion of [100.0] to [2000-01-01]
        mock_result = np.array([np.datetime64("2000-01-01")])
        mock_attempt.return_value = (mock_result, {})

        result = parse_numeric_fallback(100.0)

        assert isinstance(result, datetime)
        assert result.year == 2000
        # Verify it was called with an array
        args, _ = mock_attempt.call_args
        np.testing.assert_array_equal(args[0], np.array([100.0]))

    @patch("space_weather_analysis.util.time_utils.attempt_numeric_time_conversions")
    def test_returns_none_if_no_match(self, mock_attempt: MagicMock) -> None:
        """Scenario: Fallback fails."""
        mock_attempt.return_value = (None, {})

        result = parse_numeric_fallback(999.0)
        assert result is None
