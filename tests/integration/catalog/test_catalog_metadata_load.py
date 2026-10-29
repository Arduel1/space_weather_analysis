import json
import pathlib
from typing import Any, Dict, List

import pytest
from pymongo.database import Database

from catalog_collector.loader import CatalogLoader
from space_weather_analysis.metadata.repository import MongoMetadataRepository

HAPI_SERVER = "https://hapi.example.org/hapi"
MIRROR_SERVER = "https://mirror.example.org/hapi"
HAPI_DATASET = "synthetic/mag-1"
CDAWEB_DATASET = "SYNTH_K0"
TIME_RANGE = {"start_time": "2024-01-01T00:00Z", "end_time": "2024-02-01T00:00Z"}

CATALOG: Dict[str, List[Dict[str, Any]]] = {
    HAPI_DATASET: [{"type": "HAPI", "source_id": HAPI_SERVER, "time_range": TIME_RANGE}],
    CDAWEB_DATASET: [{"type": "FIDO_CDAWEB", "source_id": "SYNTH", "time_range": TIME_RANGE}],
}


def _hapi_info(**dataset_fields: Any) -> Dict[str, Any]:
    """Builds a synthetic HAPI /info response with a time parameter and one vector."""
    return {
        "HAPI": "3.1",
        "startDate": "2024-01-01T00:00Z",
        "stopDate": "2024-02-01T00:00Z",
        "parameters": [
            {"name": "Time", "type": "isotime", "units": "UTC", "fill": None, "length": 17},
            {"name": "B", "type": "double", "units": "nT", "fill": "-1e31", "size": [3], "label": ["x", "y", "z"]},
        ],
        **dataset_fields,
    }


def _load(db: Database, tmp_path: pathlib.Path, snapshot: Any) -> None:
    """Writes the catalog and the metadata snapshot as files and loads both like the collector."""
    catalog_path = tmp_path / "master_catalog.json"
    metadata_path = tmp_path / "dataset_metadata.json"
    catalog_path.write_text(json.dumps(CATALOG), encoding="utf-8")
    metadata_path.write_text(json.dumps(snapshot), encoding="utf-8")
    loader = CatalogLoader(db=db, file_path=catalog_path, metadata_path=metadata_path)
    loader.load_to_db()
    loader.load_metadata_to_db()


@pytest.mark.integration
def test_collector_saves_normalized_record_and_raw_info(db: Database, tmp_path: pathlib.Path) -> None:
    """A snapshot entry of a HAPI dataset yields a normalized record and the raw info, and no document of the old shape."""
    info = _hapi_info(x_server_url=MIRROR_SERVER)
    _load(db, tmp_path, [{"_id": {"$oid": "0123456789abcdef01234567"}, "dataset_id": HAPI_DATASET, "metadata": info}])
    repository = MongoMetadataRepository(db)

    record = repository.get(HAPI_DATASET)
    assert record is not None
    assert (record.collection, record.source.server_url) == ("synthetic_mag_1", MIRROR_SERVER)
    assert [variable.name for variable in record.variables] == ["B_x", "B_y", "B_z"]
    raw = repository.get_raw(HAPI_DATASET, "hapi")
    assert raw is not None
    assert raw.payload == info
    assert db["dataset_metadata"].count_documents({"metadata": {"$exists": True}}) == 0


@pytest.mark.integration
def test_collector_uses_catalog_server_when_info_names_none(db: Database, tmp_path: pathlib.Path) -> None:
    """Without x_server_url in the info, the HAPI server of the master catalog becomes the server URL."""
    _load(db, tmp_path, [{"dataset_id": HAPI_DATASET, "metadata": _hapi_info()}])

    record = MongoMetadataRepository(db).get(HAPI_DATASET)
    assert record is not None
    assert record.source.server_url == HAPI_SERVER


@pytest.mark.integration
def test_collector_saves_only_raw_info_for_dataset_without_hapi_source(db: Database, tmp_path: pathlib.Path) -> None:
    """A snapshot entry of a dataset without HAPI source in the catalog is kept as raw info without normalized record."""
    _load(db, tmp_path, {CDAWEB_DATASET: _hapi_info()})
    repository = MongoMetadataRepository(db)

    assert repository.get(CDAWEB_DATASET) is None
    assert repository.get_raw(CDAWEB_DATASET, "hapi") is not None


@pytest.mark.integration
def test_collector_skips_malformed_entries_and_loads_the_rest(db: Database, tmp_path: pathlib.Path) -> None:
    """Entries without dataset ID or with an info lacking parameters are skipped for the record, while valid entries load."""
    snapshot = [
        {"dataset_id": None, "metadata": None},
        {"dataset_id": HAPI_DATASET, "metadata": {"HAPI": "3.1", "parameters": []}},
        {"dataset_id": "unlisted", "metadata": "not a mapping"},
    ]
    _load(db, tmp_path, snapshot)
    repository = MongoMetadataRepository(db)

    assert repository.get(HAPI_DATASET) is None
    assert repository.get_raw(HAPI_DATASET, "hapi") is not None
    assert db["dataset_metadata_raw"].count_documents({}) == 1


@pytest.mark.integration
def test_collector_replaces_documents_of_the_old_shape(db: Database, tmp_path: pathlib.Path) -> None:
    """A document of the old {dataset_id, metadata} shape with its unique index is replaced by the normalized record."""
    db["dataset_metadata"].create_index("dataset_id", unique=True)
    db["dataset_metadata"].insert_one({"dataset_id": HAPI_DATASET, "metadata": {"HAPI": "2.0"}})

    _load(db, tmp_path, [{"dataset_id": HAPI_DATASET, "metadata": _hapi_info()}])

    assert db["dataset_metadata"].count_documents({}) == 1
    assert MongoMetadataRepository(db).get(HAPI_DATASET) is not None
