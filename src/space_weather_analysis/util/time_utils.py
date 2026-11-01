import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator

from space_weather_analysis.util.types import TimeRange

logger = logging.getLogger(__name__)


def ensure_aware(dt: datetime) -> datetime:
    """Ensures a datetime object is timezone-aware (UTC).

    If the datetime object is naive (no timezone info), it is assumed to be UTC
    and assigned the UTC timezone. If it is already aware, it is returned as is.

    Args:
        dt (datetime): The datetime object to check.

    Returns:
        datetime: A timezone-aware datetime object (UTC).
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# A pydantic field type that reads naive times as UTC, as a MongoDB client without tz_aware returns stored times without time zone.
UtcDatetime = Annotated[datetime, AfterValidator(ensure_aware)]


def parse_iso_time(time_str: str) -> datetime | None:
    """Parses an ISO time string into a timezone-aware datetime.

    Provider metadata often holds unparsable time texts, so a failure is logged as warning and returns None.

    Args:
        time_str (str): A time string in a format accepted by `datetime.fromisoformat`;
            naive times are treated as UTC.

    Returns:
        datetime | None: A timezone-aware datetime object (UTC), or None if parsing fails.
    """
    try:
        dt = datetime.fromisoformat(time_str)
    except (ValueError, TypeError):
        logger.warning("Failed to parse time string: %r", time_str)
        return None
    return ensure_aware(dt)


def format_iso_duration(duration: timedelta) -> str:
    """Formats a positive duration as an ISO 8601 duration.

    ISO 8601 writes durations as `P{days}DT{hours}H{minutes}M{seconds}S`; parts that are zero are left out.

    Args:
        duration: The duration, at least one microsecond.

    Returns:
        The ISO 8601 duration text, with fractional seconds if the duration has microseconds.

    Raises:
        ValueError: If the duration is zero or negative.
    """
    if duration <= timedelta(0):
        raise ValueError(f"Duration must be positive, got {duration}.")

    hours, remainder_seconds = divmod(duration.seconds, 3600)
    minutes, seconds = divmod(remainder_seconds, 60)
    date_part = f"{duration.days}D" if duration.days else ""

    time_part = ""
    if hours:
        time_part += f"{hours}H"
    if minutes:
        time_part += f"{minutes}M"
    if duration.microseconds:
        fraction = f"{duration.microseconds:06d}".rstrip("0")
        time_part += f"{seconds}.{fraction}S"
    elif seconds:
        time_part += f"{seconds}S"

    return f"P{date_part}T{time_part}" if time_part else f"P{date_part}"


def calculate_time_intersection(
    range1_start: datetime,
    range1_end: datetime,
    range2_start: datetime,
    range2_end: datetime,
) -> TimeRange | None:
    """Calculates the overlapping period between two time ranges.

    All inputs are converted to timezone-aware (UTC) datetimes before calculation
    to ensure consistent comparison.

    Args:
        range1_start (datetime): Start of the first time range.
        range1_end (datetime): End of the first time range.
        range2_start (datetime): Start of the second time range.
        range2_end (datetime): End of the second time range.

    Returns:
        TimeRange | None: A tuple containing the (start, end)
                                             of the overlapping period, or None
                                             if the ranges do not overlap (start >= end).
    """
    start = max(ensure_aware(range1_start), ensure_aware(range2_start))
    end = min(ensure_aware(range1_end), ensure_aware(range2_end))

    if start < end:
        return start, end
    return None
