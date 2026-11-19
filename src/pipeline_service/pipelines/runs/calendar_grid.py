from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from space_weather_analysis.timeseries.models import TimeBucket

# Largest cadence (time between records) per calendar cell size, so that one request holds at most about 50,000 records:
# a day of 1-second data has 86,400, a month of 1-minute data 44,640, a year of hourly data 8,784.
_DAY_CELL_MAX_CADENCE = timedelta(minutes=1)
_MONTH_CELL_MAX_CADENCE = timedelta(hours=1)


class GridChunk(NamedTuple):
    """The part of one cell of the calendar grid that a run requests.

    Attributes:
        cell_start: The start of the grid cell, which identifies the chunk.
        start: The start of the requested part (inclusive).
        stop: The end of the requested part (exclusive).
    """

    cell_start: datetime
    start: datetime
    stop: datetime


def generate_time_chunks(start_time: datetime, stop_time: datetime, unit: TimeBucket) -> list[GridChunk]:
    """Splits a time range into the parts of the calendar grid cells it touches.

    The cells are calendar hours, days, months or years in UTC, so chunks of later runs line up with earlier ones
    whatever range a run requests.

    Args:
        start_time: The start of the range (inclusive), timezone-aware.
        stop_time: The end of the range (exclusive), timezone-aware.
        unit: The calendar unit of the cells.

    Returns:
        The chunks in time order, the first and last cut to the range; empty for an empty or negative range.
    """
    chunks = []
    cell_start = calendar_cell_start(start_time, unit)
    while cell_start < stop_time:
        next_cell_start = next_calendar_cell(cell_start, unit)
        chunks.append(GridChunk(cell_start, max(cell_start, start_time), min(next_cell_start, stop_time)))
        cell_start = next_cell_start
    return chunks


def calendar_cell_start(time: datetime, unit: TimeBucket) -> datetime:
    """Returns the start of the calendar cell in UTC that contains a time.

    Args:
        time: The time, timezone-aware.
        unit: The calendar unit of the cells.

    Returns:
        The start of the hour, day, month or year of the time in UTC.
    """
    utc_time = time.astimezone(UTC)
    hour_start = utc_time.replace(minute=0, second=0, microsecond=0)
    match unit:
        case TimeBucket.HOUR:
            return hour_start
        case TimeBucket.DAY:
            return hour_start.replace(hour=0)
        case TimeBucket.MONTH:
            return hour_start.replace(day=1, hour=0)
        case TimeBucket.YEAR:
            return hour_start.replace(month=1, day=1, hour=0)


def next_calendar_cell(cell_start: datetime, unit: TimeBucket) -> datetime:
    """Returns the start of the calendar cell after the cell starting at a time.

    Args:
        cell_start: The start of a cell, as returned by calendar_cell_start.
        unit: The calendar unit of the cells.

    Returns:
        The start of the next hour, day, month or year.
    """
    match unit:
        case TimeBucket.HOUR:
            return cell_start + timedelta(hours=1)
        case TimeBucket.DAY:
            return cell_start + timedelta(days=1)
        case TimeBucket.MONTH:
            if cell_start.month == 12:
                return cell_start.replace(year=cell_start.year + 1, month=1)
            return cell_start.replace(month=cell_start.month + 1)
        case TimeBucket.YEAR:
            return cell_start.replace(year=cell_start.year + 1)


def chunk_unit_for_cadence(cadence: timedelta | None) -> TimeBucket:
    """Selects the calendar cell unit for data of a cadence, so that one request holds at most about 50,000 records.

    Args:
        cadence: The time between two records; None if unknown.

    Returns:
        A day below one minute or for an unknown cadence, a month below one hour, a year from one hour on.
    """
    if cadence is None or cadence < _DAY_CELL_MAX_CADENCE:
        return TimeBucket.DAY
    if cadence < _MONTH_CELL_MAX_CADENCE:
        return TimeBucket.MONTH
    return TimeBucket.YEAR
