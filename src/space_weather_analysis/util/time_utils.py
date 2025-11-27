import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def ensure_aware(dt: datetime) -> datetime:
    """
    Ensures a datetime object is timezone-aware (UTC).

    If the datetime object is naive (no timezone info), it is assumed to be UTC
    and assigned the UTC timezone. If it is already aware, it is returned as is.

    Args:
        dt (datetime): The datetime object to check.

    Returns:
        datetime: A timezone-aware datetime object (UTC).
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def parse_iso_time(time_str: str) -> datetime:
    """
    Safely parses an ISO time string into a timezone-aware datetime.

    This function handles the 'Z' suffix often used in space weather data
    (HAPI, CDAWeb) to denote UTC, ensuring compatibility with Python's
    fromisoformat.

    Args:
        time_str (str): The ISO formatted time string (e.g., "2023-01-01T12:00:00Z").

    Returns:
        datetime: A timezone-aware datetime object. Returns datetime.min (UTC)
                  if parsing fails, logged as an error.
    """
    try:
        # Ensure 'Z' (Zulu time) is correctly interpreted as +00:00 offset
        if time_str.endswith("Z"):
            time_str = time_str[:-1] + "+00:00"

        dt = datetime.fromisoformat(time_str)
        return ensure_aware(dt)
    except Exception:
        logger.error(f"Failed to parse time string: {time_str}", exc_info=True)
        return datetime.min.replace(tzinfo=timezone.utc)


def calculate_time_intersection(
    range1_start: datetime,
    range1_end: datetime,
    range2_start: datetime,
    range2_end: datetime,
) -> Optional[Tuple[datetime, datetime]]:
    """
    Calculates the overlapping period between two time ranges.

    All inputs are converted to timezone-aware (UTC) datetimes before calculation.

    Args:
        range1_start (datetime): Start of the first time range.
        range1_end (datetime): End of the first time range.
        range2_start (datetime): Start of the second time range.
        range2_end (datetime): End of the second time range.

    Returns:
        Optional[Tuple[datetime, datetime]]: A tuple containing the (start, end)
                                             of the overlapping period, or None
                                             if the ranges do not overlap.
    """
    start = max(ensure_aware(range1_start), ensure_aware(range2_start))
    end = min(ensure_aware(range1_end), ensure_aware(range2_end))

    # Valid intersection only if start is strictly before end
    if start < end:
        return start, end
    return None
