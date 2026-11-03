from datetime import UTC, datetime, timedelta, timezone

import pytest

from space_weather_analysis.util.time_utils import (
    calculate_time_intersection,
    ensure_aware,
    format_iso_duration,
    parse_iso_duration,
    parse_iso_time,
)


class TestEnsureAware:
    def test_naive_datetime_becomes_utc(self) -> None:
        """A naive datetime is tagged as UTC without shifting."""
        naive_dt = datetime(2023, 1, 1, 12, 0, 0)  # noqa: DTZ001 -- the naive input is the case under test
        result = ensure_aware(naive_dt)

        assert result.tzinfo == UTC
        assert result.hour == 12

    def test_aware_utc_remains_unchanged(self) -> None:
        """An aware UTC datetime is returned unchanged."""
        aware_dt = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = ensure_aware(aware_dt)

        assert result.tzinfo == UTC
        assert result == aware_dt

    def test_aware_non_utc_converts_to_utc(self) -> None:
        """An aware non-UTC datetime is converted to UTC."""
        offset = timezone(timedelta(hours=1))
        non_utc_dt = datetime(2023, 1, 1, 12, 0, 0, tzinfo=offset)

        result = ensure_aware(non_utc_dt)

        assert result.tzinfo == UTC
        assert result.hour == 11


class TestParseIsoTime:
    def test_parse_valid_utc_string_with_z(self) -> None:
        """A 'Z' suffix yields a UTC datetime."""
        ts = "2023-01-01T12:00:00Z"
        dt = parse_iso_time(ts)

        assert dt is not None
        assert dt.year == 2023
        assert dt.hour == 12
        assert dt.tzinfo == UTC

    def test_parse_valid_offset_string(self) -> None:
        """An explicit +00:00 offset yields a UTC datetime."""
        ts = "2023-01-01T12:00:00+00:00"
        dt = parse_iso_time(ts)

        assert dt is not None
        assert dt.tzinfo == UTC
        assert dt.hour == 12

    def test_parse_naive_string_assumes_utc(self) -> None:
        """A string without offset is read as UTC."""
        ts = "2023-01-01T12:00:00"
        dt = parse_iso_time(ts)

        assert dt is not None
        assert dt.tzinfo == UTC
        assert dt.hour == 12

    def test_parse_failure_returns_none(self) -> None:
        """A malformed string returns None."""
        assert parse_iso_time("not-a-date") is None


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


class TestParseIsoDuration:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("PT3H", timedelta(hours=3), id="time-part"),
            pytest.param("P1DT0.5S", timedelta(days=1, milliseconds=500), id="date-and-fractional-time"),
            pytest.param("P1M", timedelta(days=365.2425 / 12), id="month-as-average"),
            pytest.param("PT0S", None, id="zero"),
            pytest.param("3 hours", None, id="no-iso-duration"),
        ],
    )
    def test_duration_parsed_with_average_calendar_parts(self, text: str, expected: timedelta | None) -> None:
        """An ISO 8601 duration becomes a timedelta, months as their Gregorian average; zero or other text gives None."""
        assert parse_iso_duration(text) == expected


class TestCalculateTimeIntersection:
    def test_full_overlap_nested(self) -> None:
        """A range nested inside the other is returned unchanged."""
        r1_start = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        r1_end = datetime(2023, 1, 1, 13, 0, 0, tzinfo=UTC)

        r2_start = datetime(2023, 1, 1, 10, 0, 0, tzinfo=UTC)
        r2_end = datetime(2023, 1, 1, 14, 0, 0, tzinfo=UTC)

        result = calculate_time_intersection(r1_start, r1_end, r2_start, r2_end)

        assert result is not None
        assert result == (r1_start, r1_end)

    def test_partial_overlap(self) -> None:
        """Partially overlapping ranges yield the overlap."""
        r1_start = datetime(2023, 1, 1, 10, 0, 0, tzinfo=UTC)
        r1_end = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)

        r2_start = datetime(2023, 1, 1, 11, 0, 0, tzinfo=UTC)
        r2_end = datetime(2023, 1, 1, 13, 0, 0, tzinfo=UTC)

        result = calculate_time_intersection(r1_start, r1_end, r2_start, r2_end)

        assert result is not None
        assert result[0] == r2_start
        assert result[1] == r1_end

    def test_no_overlap_disjoint(self) -> None:
        """Disjoint ranges yield None."""
        r1_start = datetime(2023, 1, 1, 10, 0, 0, tzinfo=UTC)
        r1_end = datetime(2023, 1, 1, 11, 0, 0, tzinfo=UTC)

        r2_start = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)
        r2_end = datetime(2023, 1, 1, 13, 0, 0, tzinfo=UTC)

        result = calculate_time_intersection(r1_start, r1_end, r2_start, r2_end)

        assert result is None

    def test_adjacent_ranges_return_none(self) -> None:
        """Ranges that only touch yield None, as an intersection needs start < end."""
        boundary = datetime(2023, 1, 1, 11, 0, 0, tzinfo=UTC)
        r1_start = datetime(2023, 1, 1, 10, 0, 0, tzinfo=UTC)
        r2_end = datetime(2023, 1, 1, 12, 0, 0, tzinfo=UTC)

        result = calculate_time_intersection(r1_start, boundary, boundary, r2_end)

        assert result is None

    def test_naive_inputs_handled(self) -> None:
        """Naive inputs are read as UTC and combine with aware ones."""
        r1_start = datetime(2023, 1, 1, 10, 0, 0)  # noqa: DTZ001 -- the naive input is the case under test
        r1_end = datetime(2023, 1, 1, 12, 0, 0)  # noqa: DTZ001 -- the naive input is the case under test

        r2_start = datetime(2023, 1, 1, 11, 0, 0, tzinfo=UTC)
        r2_end = datetime(2023, 1, 1, 13, 0, 0, tzinfo=UTC)

        result = calculate_time_intersection(r1_start, r1_end, r2_start, r2_end)

        assert result is not None
        assert result[0].tzinfo == UTC
        assert result[0].hour == 11
        assert result[1].hour == 12
