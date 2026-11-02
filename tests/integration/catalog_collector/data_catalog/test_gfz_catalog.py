from datetime import datetime, timedelta

import pytest
from pymongo.database import Database

from catalog_collector.data_catalog.gfz_catalog import ensure_gfz_sources
from space_weather_analysis.catalog.repository import MongoCatalogRepository


@pytest.mark.integration
def test_kp_source_is_added_once_and_kept_on_later_starts(db: Database, now: datetime) -> None:
    """The first start adds the Kp index from 1932 until now; a later start keeps it instead of adding a second one."""
    repository = MongoCatalogRepository(db)

    added_first = ensure_gfz_sources(repository, now)
    added_later = ensure_gfz_sources(repository, now + timedelta(days=1))

    assert repository.get_all() == [
        {
            "dataset_id": "Kp",
            "sources": [
                {
                    "type": "GFZ_POTSDAM",
                    "source_id": "GFZ",
                    "time_range": {"start_time": "1932-01-01T00:00:00Z", "end_time": "2026-10-30T12:00:00Z"},
                    "ongoing": True,
                }
            ],
        }
    ]
    assert (added_first, added_later) == (1, 0)
