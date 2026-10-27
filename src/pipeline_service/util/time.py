from datetime import datetime, timedelta
from typing import List

from space_weather_analysis.util.types import TimeRange


def generate_time_chunks(start_time: datetime, stop_time: datetime, chunk_delta: timedelta) -> List[TimeRange]:
    """Splits a time range into smaller chunks.

    Args:
        start_time: The overall start time.
        stop_time: The overall stop time.
        chunk_delta: The duration of each chunk.

    Returns:
        A list of (chunk_start, chunk_stop) tuples.
    """
    chunks = []
    current_start = start_time
    while current_start < stop_time:
        current_stop = current_start + chunk_delta
        if current_stop > stop_time:
            current_stop = stop_time
        chunks.append((current_start, current_stop))
        current_start = current_stop
    return chunks
