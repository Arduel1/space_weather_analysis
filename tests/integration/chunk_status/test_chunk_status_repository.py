from datetime import UTC, datetime, timedelta

import pytest
from pymongo.database import Database

from pipeline_service.chunk_status.models import ChunkStatus
from pipeline_service.chunk_status.repository import MongoChunkStatusRepository

CELL_START = datetime(2024, 1, 1, tzinfo=UTC)


@pytest.mark.integration
def test_saved_status_is_read_back_and_replaced_per_dataset_and_cell(db: Database) -> None:
    """A second save of the same dataset and cell replaces the first; other datasets and cells stay unknown."""
    repository = MongoChunkStatusRepository(db)
    first = ChunkStatus(
        dataset_id="synthetic/values",
        cell_start=CELL_START,
        fetched_start=CELL_START + timedelta(hours=6),
        fetched_stop=CELL_START + timedelta(hours=12),
        fetched_at=datetime(2024, 1, 1, 13, tzinfo=UTC),
        stable=False,
    )
    second = first.model_copy(update={"fetched_start": CELL_START, "fetched_stop": CELL_START + timedelta(days=1), "stable": True})

    repository.save(first)
    repository.save(second)

    assert repository.get("synthetic/values", CELL_START) == second
    assert (repository.get("other", CELL_START), repository.get("synthetic/values", CELL_START + timedelta(days=1))) == (None, None)
