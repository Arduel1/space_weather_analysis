from datetime import datetime, timedelta

import pytest
from pymongo.database import Database

from catalog_collector.data_catalog.gfz_catalog import ensure_gfz_sources
from space_weather_analysis.catalog.repository import MongoCatalogRepository


@pytest.mark.integration
def test_gfz_sources_are_added_once_and_kept_on_later_starts(db: Database, now: datetime) -> None:
    """The first start adds each GFZ index from 1932 until now; a later start keeps them instead of adding second ones."""
    repository = MongoCatalogRepository(db)

    added_first = ensure_gfz_sources(repository, now)
    added_later = ensure_gfz_sources(repository, now + timedelta(days=1))

    source = {
        "type": "GFZ_POTSDAM",
        "source_id": "GFZ",
        "time_range": {"start_time": "1932-01-01T00:00:00Z", "end_time": "2026-10-30T12:00:00Z"},
        "ongoing": True,
    }
    assert sorted(repository.get_all(), key=lambda entry: entry["dataset_id"]) == [
        {"dataset_id": "Hp30", "sources": [source]},
        {"dataset_id": "Kp", "sources": [source]},
    ]
    assert (added_first, added_later) == (2, 0)
