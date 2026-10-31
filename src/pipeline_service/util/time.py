from datetime import datetime, timedelta

from space_weather_analysis.util.types import TimeRange


def generate_time_chunks(start_time: datetime, stop_time: datetime, chunk_delta: timedelta) -> list[TimeRange]:
    """Splits a time range into consecutive chunks of a fixed length; the last chunk ends at the stop time.

    Args:
        start_time: The start of the range (inclusive).
        stop_time: The end of the range (exclusive).
        chunk_delta: The length of each chunk.

    Returns:
        The (chunk_start, chunk_stop) pairs in time order.
    """
    chunks = []
    current_start = start_time
    while current_start < stop_time:
        current_stop = min(current_start + chunk_delta, stop_time)
        chunks.append((current_start, current_stop))
        current_start = current_stop
    return chunks
