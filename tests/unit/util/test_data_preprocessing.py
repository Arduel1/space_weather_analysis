from datetime import datetime, timedelta, timezone
from typing import List

from pipeline_service.util.batching import batch_generator
from pipeline_service.util.naming import sanitize_collection_name
from pipeline_service.util.time import generate_time_chunks


def test_batch_generator_empty_list() -> None:
    """An empty list yields no batches."""
    data: List[int] = []
    batches = list(batch_generator(data, batch_size=10))
    assert len(batches) == 0


def test_batch_generator_smaller_than_batch_size() -> None:
    """A list shorter than the batch size yields one batch with all elements."""
    data = [1, 2, 3]
    batches = list(batch_generator(data, batch_size=10))
    assert len(batches) == 1
    assert batches[0] == [1, 2, 3]


def test_batch_generator_exact_batch_size() -> None:
    """A list of exactly the batch size yields one batch."""
    data = [1, 2, 3, 4, 5]
    batches = list(batch_generator(data, batch_size=5))
    assert len(batches) == 1
    assert batches[0] == data


def test_batch_generator_multiple_batches_exact() -> None:
    """A multiple of the batch size yields only full batches."""
    data = [1, 2, 3, 4, 5, 6]
    batches = list(batch_generator(data, batch_size=2))
    assert len(batches) == 3
    assert batches[0] == [1, 2]
    assert batches[1] == [3, 4]
    assert batches[2] == [5, 6]


def test_batch_generator_with_remainder() -> None:
    """The last batch holds the remainder."""
    data = [1, 2, 3, 4, 5, 6, 7]
    batches = list(batch_generator(data, batch_size=3))
    assert len(batches) == 3
    assert batches[0] == [1, 2, 3]
    assert batches[1] == [4, 5, 6]
    assert batches[2] == [7]


def test_sanitize_collection_name_clean() -> None:
    """A name without invalid characters is unchanged."""
    assert sanitize_collection_name("valid_name") == "valid_name"


def test_sanitize_collection_name_replacements() -> None:
    """Slashes and hyphens become underscores."""
    raw_id = "group/subgroup-dataset"
    expected = "group_subgroup_dataset"
    assert sanitize_collection_name(raw_id) == expected


def test_sanitize_collection_name_mixed() -> None:
    """Every one of several mixed invalid characters is replaced."""
    raw_id = "A/B-C/D-E"
    expected = "A_B_C_D_E"
    assert sanitize_collection_name(raw_id) == expected


def test_sanitize_collection_name_empty() -> None:
    """An empty string stays empty."""
    assert sanitize_collection_name("") == ""


def test_generate_time_chunks_single_chunk() -> None:
    """A range shorter than the delta yields one chunk covering it exactly."""
    start = datetime(2023, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2023, 1, 2, 0, 0, tzinfo=timezone.utc)
    delta = timedelta(days=7)

    chunks = generate_time_chunks(start, end, delta)

    assert len(chunks) == 1
    assert chunks[0] == (start, end)


def test_generate_time_chunks_exact_multiple() -> None:
    """A range of an exact multiple of the delta yields only full chunks."""
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end = datetime(2023, 1, 15, tzinfo=timezone.utc)
    delta = timedelta(days=7)

    chunks = generate_time_chunks(start, end, delta)

    assert len(chunks) == 2
    assert chunks[0] == (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2023, 1, 8, tzinfo=timezone.utc))
    assert chunks[1] == (datetime(2023, 1, 8, tzinfo=timezone.utc), datetime(2023, 1, 15, tzinfo=timezone.utc))


def test_generate_time_chunks_remainder() -> None:
    """The last chunk is cut at the end of the range."""
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end = datetime(2023, 1, 11, tzinfo=timezone.utc)
    delta = timedelta(days=7)

    chunks = generate_time_chunks(start, end, delta)

    assert len(chunks) == 2
    assert chunks[0] == (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2023, 1, 8, tzinfo=timezone.utc))
    assert chunks[1] == (datetime(2023, 1, 8, tzinfo=timezone.utc), datetime(2023, 1, 11, tzinfo=timezone.utc))


def test_generate_time_chunks_no_range() -> None:
    """A zero or negative range yields no chunks."""
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end = start
    delta = timedelta(days=1)

    chunks = generate_time_chunks(start, end, delta)
    assert len(chunks) == 0

    chunks_negative = generate_time_chunks(start, start - delta, delta)
    assert len(chunks_negative) == 0
