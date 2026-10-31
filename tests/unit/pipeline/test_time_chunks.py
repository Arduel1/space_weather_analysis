from datetime import UTC, datetime, timedelta

import pytest

from pipeline_service.util.time import generate_time_chunks

START = datetime(2023, 1, 1, tzinfo=UTC)
WEEK = timedelta(days=7)


@pytest.mark.parametrize(
    ("stop", "expected_chunks"),
    [
        pytest.param(START + timedelta(days=1), [(START, START + timedelta(days=1))], id="shorter_than_chunk"),
        pytest.param(START + 2 * WEEK, [(START, START + WEEK), (START + WEEK, START + 2 * WEEK)], id="exact_multiple"),
        pytest.param(START + timedelta(days=10), [(START, START + WEEK), (START + WEEK, START + timedelta(days=10))], id="remainder"),
        pytest.param(START, [], id="empty_range"),
        pytest.param(START - WEEK, [], id="negative_range"),
    ],
)
def test_time_range_split_into_consecutive_chunks(stop: datetime, expected_chunks: list[tuple[datetime, datetime]]) -> None:
    """A range is split into chunks of the chunk length, the last one ending at the stop; an empty or negative range yields none."""
    assert generate_time_chunks(START, stop, WEEK) == expected_chunks
