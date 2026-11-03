from datetime import UTC, datetime, timedelta

import pytest

from pipeline_service.pipelines.chunks import GridChunk, chunk_unit_for_cadence, generate_time_chunks
from space_weather_analysis.timeseries.models import TimeBucket


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


@pytest.mark.parametrize(
    ("start", "stop", "unit", "expected_chunks"),
    [
        pytest.param(
            _utc(2024, 1, 1, 6),
            _utc(2024, 1, 2, 12),
            TimeBucket.DAY,
            [GridChunk(_utc(2024, 1, 1), _utc(2024, 1, 1, 6), _utc(2024, 1, 2)), GridChunk(_utc(2024, 1, 2), _utc(2024, 1, 2), _utc(2024, 1, 2, 12))],
            id="day-cells-cut-to-range",
        ),
        pytest.param(
            _utc(2023, 12, 1),
            _utc(2024, 2, 1),
            TimeBucket.MONTH,
            [GridChunk(_utc(2023, 12, 1), _utc(2023, 12, 1), _utc(2024, 1, 1)), GridChunk(_utc(2024, 1, 1), _utc(2024, 1, 1), _utc(2024, 2, 1))],
            id="month-cells-across-year-end",
        ),
        pytest.param(
            _utc(1859, 9, 1),
            _utc(1859, 9, 3),
            TimeBucket.YEAR,
            [GridChunk(_utc(1859, 1, 1), _utc(1859, 9, 1), _utc(1859, 9, 3))],
            id="year-cell-before-1900",
        ),
        pytest.param(_utc(2024, 1, 1), _utc(2024, 1, 1), TimeBucket.DAY, [], id="empty-range"),
        pytest.param(_utc(2024, 1, 2), _utc(2024, 1, 1), TimeBucket.DAY, [], id="negative-range"),
    ],
)
def test_time_range_split_along_calendar_cells(start: datetime, stop: datetime, unit: TimeBucket, expected_chunks: list[GridChunk]) -> None:
    """A range is split along the calendar cells of the unit, the outer chunks cut to the range; an empty or negative range yields none."""
    assert generate_time_chunks(start, stop, unit) == expected_chunks


@pytest.mark.parametrize(
    ("cadence", "expected_unit"),
    [
        pytest.param(None, TimeBucket.DAY, id="unknown"),
        pytest.param(timedelta(seconds=1), TimeBucket.DAY, id="seconds"),
        pytest.param(timedelta(minutes=1), TimeBucket.MONTH, id="one-minute"),
        pytest.param(timedelta(hours=3), TimeBucket.YEAR, id="hours"),
    ],
)
def test_chunk_unit_follows_cadence(cadence: timedelta | None, expected_unit: TimeBucket) -> None:
    """Finer cadences get smaller cells; an unknown cadence gets the smallest."""
    assert chunk_unit_for_cadence(cadence) == expected_unit
