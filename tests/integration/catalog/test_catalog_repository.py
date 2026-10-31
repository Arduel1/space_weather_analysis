import pytest
from pymongo.database import Database

from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem, TimeRangeDict
from space_weather_analysis.catalog.repository import MongoCatalogRepository

TIME_RANGE: TimeRangeDict = {"start_time": "2024-01-01T00:00Z", "end_time": "2024-02-01T00:00Z"}
HAPI_SOURCE: CatalogEntryDict = {"type": "HAPI", "source_id": "https://hapi.example.org/hapi", "time_range": TIME_RANGE}
MIRROR_SOURCE: CatalogEntryDict = {"type": "HAPI", "source_id": "https://mirror.example.org/hapi", "time_range": TIME_RANGE}
CDAWEB_SOURCE: CatalogEntryDict = {"type": "CDAWEB", "source_id": "SYNTH", "time_range": TIME_RANGE}


def _item(dataset_id: str, sources: list[CatalogEntryDict]) -> MasterCatalogItem:
    """Builds a catalog item of a dataset with the given sources."""
    return {"dataset_id": dataset_id, "sources": sources}


@pytest.mark.integration
def test_catalog_item_round_trip_through_repository(db: Database) -> None:
    """Saved items read back unchanged by dataset ID and all together sorted by dataset ID."""
    repository = MongoCatalogRepository(db)
    mixed = _item("synthetic/mixed", [CDAWEB_SOURCE, HAPI_SOURCE])
    hapi_only = _item("synthetic/hapi", [HAPI_SOURCE])

    repository.save_many([mixed, hapi_only])

    assert repository.get("synthetic/mixed") == mixed
    assert repository.get("unknown") is None
    assert repository.get_all() == [hapi_only, mixed]


@pytest.mark.integration
def test_saving_an_item_again_replaces_it(db: Database) -> None:
    """Saving an item of the same dataset again keeps one document with the latest sources."""
    repository = MongoCatalogRepository(db)
    repository.save(_item("synthetic/one", [HAPI_SOURCE]))

    repository.save(_item("synthetic/one", [MIRROR_SOURCE]))

    assert db["master_catalog"].count_documents({}) == 1
    assert repository.get("synthetic/one") == _item("synthetic/one", [MIRROR_SOURCE])


@pytest.mark.integration
def test_sources_filtered_by_type(db: Database) -> None:
    """get_source_map and get_sources keep only the sources of the requested type in their stored order."""
    repository = MongoCatalogRepository(db)
    repository.save_many(
        [
            _item("synthetic/mixed", [HAPI_SOURCE, CDAWEB_SOURCE, MIRROR_SOURCE]),
            _item("SYNTH_K0", [CDAWEB_SOURCE]),
        ]
    )

    assert repository.get_source_map("HAPI") == {"synthetic/mixed": [HAPI_SOURCE, MIRROR_SOURCE]}
    assert repository.get_sources("synthetic/mixed", "CDAWEB") == [CDAWEB_SOURCE]
    assert repository.get_sources("synthetic/mixed") == [HAPI_SOURCE, CDAWEB_SOURCE, MIRROR_SOURCE]
    assert repository.get_sources("unknown") == []


@pytest.mark.integration
def test_delete_removes_only_the_given_dataset(db: Database) -> None:
    """Deleting a dataset removes its item and reports whether one existed, leaving other datasets in place."""
    repository = MongoCatalogRepository(db)
    repository.save_many([_item("synthetic/one", [HAPI_SOURCE]), _item("synthetic/two", [CDAWEB_SOURCE])])

    assert (repository.delete("synthetic/one"), repository.delete("synthetic/one")) == (True, False)
    assert repository.get_all() == [_item("synthetic/two", [CDAWEB_SOURCE])]
