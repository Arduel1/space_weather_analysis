from datetime import datetime, timedelta, timezone
from typing import List

from pipeline_service.util.batching import batch_generator
from pipeline_service.util.naming import sanitize_collection_name
from pipeline_service.util.time import generate_time_chunks

# --- Tests for batch_generator ---


def test_batch_generator_empty_list() -> None:
    """
    Test that an empty list yields no batches.
    """
    data: List[int] = []
    batches = list(batch_generator(data, batch_size=10))
    assert len(batches) == 0


def test_batch_generator_smaller_than_batch_size() -> None:
    """
    Test that a list smaller than the batch size yields a single batch
    containing all elements.
    """
    data = [1, 2, 3]
    batches = list(batch_generator(data, batch_size=10))
    assert len(batches) == 1
    assert batches[0] == [1, 2, 3]


def test_batch_generator_exact_batch_size() -> None:
    """
    Test that a list with length exactly equal to the batch size yields
    a single batch.
    """
    data = [1, 2, 3, 4, 5]
    batches = list(batch_generator(data, batch_size=5))
    assert len(batches) == 1
    assert batches[0] == data


def test_batch_generator_multiple_batches_exact() -> None:
    """
    Test splitting into multiple full batches without remainder.
    """
    data = [1, 2, 3, 4, 5, 6]
    batches = list(batch_generator(data, batch_size=2))
    assert len(batches) == 3
    assert batches[0] == [1, 2]
    assert batches[1] == [3, 4]
    assert batches[2] == [5, 6]


def test_batch_generator_with_remainder() -> None:
    """
    Test splitting into multiple batches where the last one is smaller.
    """
    data = [1, 2, 3, 4, 5, 6, 7]
    batches = list(batch_generator(data, batch_size=3))
    assert len(batches) == 3
    assert batches[0] == [1, 2, 3]
    assert batches[1] == [4, 5, 6]
    assert batches[2] == [7]  # Remainder


# --- Tests for sanitize_collection_name ---


def test_sanitize_collection_name_clean() -> None:
    """
    Test that a string with no invalid characters is returned unchanged.
    """
    assert sanitize_collection_name("valid_name") == "valid_name"


def test_sanitize_collection_name_replacements() -> None:
    """
    Test that slashes and hyphens are replaced with underscores.
    """
    raw_id = "group/subgroup-dataset"
    expected = "group_subgroup_dataset"
    assert sanitize_collection_name(raw_id) == expected


def test_sanitize_collection_name_mixed() -> None:
    """
    Test a complex case with multiple invalid characters.
    """
    raw_id = "A/B-C/D-E"
    expected = "A_B_C_D_E"
    assert sanitize_collection_name(raw_id) == expected


def test_sanitize_collection_name_empty() -> None:
    """
    Test handling of an empty string.
    """
    assert sanitize_collection_name("") == ""


# --- Tests for generate_time_chunks ---


def test_generate_time_chunks_single_chunk() -> None:
    """
    Test that a time range smaller than the chunk delta returns a single chunk
    covering the exact range.
    """
    start = datetime(2023, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2023, 1, 2, 0, 0, tzinfo=timezone.utc)
    delta = timedelta(days=7)

    chunks = generate_time_chunks(start, end, delta)

    assert len(chunks) == 1
    assert chunks[0] == (start, end)


def test_generate_time_chunks_exact_multiple() -> None:
    """
    Test splitting a range that is an exact multiple of the chunk delta.
    """
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end = datetime(2023, 1, 15, tzinfo=timezone.utc)
    delta = timedelta(days=7)

    chunks = generate_time_chunks(start, end, delta)

    assert len(chunks) == 2
    assert chunks[0] == (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2023, 1, 8, tzinfo=timezone.utc))
    assert chunks[1] == (datetime(2023, 1, 8, tzinfo=timezone.utc), datetime(2023, 1, 15, tzinfo=timezone.utc))


def test_generate_time_chunks_remainder() -> None:
    """
    Test splitting a range that leaves a smaller final chunk.
    """
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    # 10 days total range
    end = datetime(2023, 1, 11, tzinfo=timezone.utc)
    delta = timedelta(days=7)

    chunks = generate_time_chunks(start, end, delta)

    assert len(chunks) == 2
    # First chunk: full 7 days
    assert chunks[0] == (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2023, 1, 8, tzinfo=timezone.utc))
    # Second chunk: remaining 3 days
    assert chunks[1] == (datetime(2023, 1, 8, tzinfo=timezone.utc), datetime(2023, 1, 11, tzinfo=timezone.utc))


def test_generate_time_chunks_no_range() -> None:
    """
    Test that start_time >= end_time returns an empty list.
    """
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end = start  # Zero duration
    delta = timedelta(days=1)

    chunks = generate_time_chunks(start, end, delta)
    assert len(chunks) == 0

    # Negative duration case
    chunks_negative = generate_time_chunks(start, start - delta, delta)
    assert len(chunks_negative) == 0
