import json
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytest
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from catalog_collector.loader import CatalogLoader
from catalog_collector.metadata_refresh import MetadataRefresher, MetadataRefreshSummary
from space_weather_analysis.catalog.models import CatalogEntryDict
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.models import DatasetMetadata, RawMetadataRecord, SourceInfo
from space_weather_analysis.metadata.repository import MongoMetadataRepository

DATASET_ID = "synthetic_plasma_P1M"
SERVER_URL = "https://hapi.example.org/hapi"
MIRROR_SERVER_ID = "Example"
MIRROR_PATH = "/meta"
STORED_AT = datetime(2026, 7, 21, tzinfo=timezone.utc)
CATALOG_SOURCE: CatalogEntryDict = {
    "type": "HAPI",
    "source_id": SERVER_URL,
    "time_range": {"start_time": "2016-07-26T00:00Z", "end_time": "2026-07-20T00:00Z"},
}

STORED_INFO: Dict[str, Any] = {
    "HAPI": "3.1",
    "startDate": "2016-07-26T00:00Z",
    "stopDate": "2026-07-20T00:00Z",
    "parameters": [
        {"name": "Time", "type": "isotime", "units": "UTC", "fill": None, "length": 17},
        {"name": "density", "type": "double", "units": "cm^-3", "fill": "-9999"},
    ],
}
CHANGED_INFO: Dict[str, Any] = {
    **STORED_INFO,
    "stopDate": "2026-10-28T00:00Z",
    "parameters": [*STORED_INFO["parameters"], {"name": "speed", "type": "double", "units": "km/s", "fill": "-9999"}],
}


def _mirror_file(dataset_infos: Dict[str, Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    """Builds catalogs-all.json with one mirror server listing the datasets, each with its info stamped by the mirror fetch time."""
    entries: List[Dict[str, Any]] = []
    for dataset_id, info in dataset_infos.items():
        entry: Dict[str, Any] = {"id": dataset_id}
        if info is not None:
            entry["info"] = {**info, "x_LastUpdate": "2026-10-29T15:20:00Z"}
        entries.append(entry)
    return {MIRROR_SERVER_ID: {"about": {"x_url": SERVER_URL}, "catalog": {"catalog": entries}}}


def _serve_mirror(httpserver: HTTPServer, dataset_infos: Dict[str, Optional[Dict[str, Any]]]) -> str:
    """Lets the fake mirror serve its full file with the dataset infos and returns the mirror base URL."""
    httpserver.clear()
    httpserver.expect_request(f"{MIRROR_PATH}/catalogs-all.json").respond_with_json(_mirror_file(dataset_infos))
    return httpserver.url_for(f"{MIRROR_PATH}/")


def _store(db: Database, info: Dict[str, Any] = STORED_INFO, record: Optional[DatasetMetadata] = None) -> None:
    """Saves the catalog with one HAPI source, the raw info as the HAPI client recorded it, and its record or the given one."""
    MongoCatalogRepository(db).save({"dataset_id": DATASET_ID, "sources": [CATALOG_SOURCE]})
    repository = MongoMetadataRepository(db)
    repository.save_raw(DATASET_ID, "hapi", {**info, "x_server_url": SERVER_URL}, SERVER_URL, STORED_AT)
    repository.save(record or build_hapi_metadata(DATASET_ID, DATASET_ID, info, SERVER_URL, STORED_AT))


def _stored_metadata(db: Database) -> Tuple[Optional[DatasetMetadata], Optional[RawMetadataRecord]]:
    """Reads the record and the raw HAPI metadata of the dataset."""
    repository = MongoMetadataRepository(db)
    return repository.get(DATASET_ID), repository.get_raw(DATASET_ID, "hapi")


def _refresh(db: Database, mirror_base_url: str) -> Optional[MetadataRefreshSummary]:
    """Runs one metadata refresh against the mirror and returns its summary."""
    return MetadataRefresher(MongoCatalogRepository(db), MongoMetadataRepository(db), mirror_base_url).refresh()


@pytest.mark.integration
def test_changed_info_replaces_raw_metadata_and_record(db: Database, httpserver: HTTPServer) -> None:
    """A mirrored info that differs from the stored one is stored without the mirror's fetch time and normalized again."""
    _store(db)

    summary = _refresh(db, _serve_mirror(httpserver, {DATASET_ID: CHANGED_INFO}))

    record, raw_record = _stored_metadata(db)
    assert record is not None and raw_record is not None
    expected_record = build_hapi_metadata(DATASET_ID, DATASET_ID, CHANGED_INFO, SERVER_URL, record.updated_at)
    assert (summary is not None and summary.replaced, record, raw_record.payload) == (1, expected_record, CHANGED_INFO)
    assert raw_record.retrieved_at > STORED_AT


@pytest.mark.integration
def test_info_differing_only_in_volatile_keys_is_not_rewritten(db: Database, httpserver: HTTPServer) -> None:
    """An info that differs only in the mirror's fetch time and the recorded server URL leaves the stored metadata untouched."""
    _store(db)
    stored = _stored_metadata(db)

    summary = _refresh(db, _serve_mirror(httpserver, {DATASET_ID: STORED_INFO}))

    assert (summary is not None and summary.unchanged, _stored_metadata(db)) == (1, stored)


@pytest.mark.integration
@pytest.mark.parametrize(
    "mirror_state",
    [
        pytest.param("dataset_missing", id="dataset_missing"),
        pytest.param("listed_without_info", id="listed_without_info"),
        pytest.param("info_not_normalizable", id="info_not_normalizable"),
        pytest.param("mirror_http_500", id="mirror_http_500"),
    ],
)
def test_unusable_mirror_state_keeps_last_good_metadata(db: Database, httpserver: HTTPServer, mirror_state: str) -> None:
    """A dataset absent from the mirror, listed without info, with an info lacking parameters, or an unreadable mirror keep the stored metadata."""
    _store(db)
    stored = _stored_metadata(db)
    if mirror_state == "mirror_http_500":
        httpserver.expect_request(f"{MIRROR_PATH}/catalogs-all.json").respond_with_data("Internal Server Error", status=500)
        mirror_base_url = httpserver.url_for(f"{MIRROR_PATH}/")
    else:
        dataset_infos: Dict[str, Dict[str, Optional[Dict[str, Any]]]] = {
            "dataset_missing": {"synthetic_other_P1M": STORED_INFO},
            "listed_without_info": {DATASET_ID: None},
            "info_not_normalizable": {DATASET_ID: {**CHANGED_INFO, "parameters": []}},
        }
        mirror_base_url = _serve_mirror(httpserver, dataset_infos[mirror_state])

    _refresh(db, mirror_base_url)

    assert _stored_metadata(db) == stored


@pytest.mark.integration
def test_record_of_another_source_kind_is_not_replaced(db: Database, httpserver: HTTPServer) -> None:
    """A record built from another source kind ranks above HAPI metadata and stays in place."""
    _store(db, record=DatasetMetadata(dataset_id=DATASET_ID, collection=DATASET_ID, source=SourceInfo(type="FIDO_CDAWEB")))
    stored = _stored_metadata(db)

    summary = _refresh(db, _serve_mirror(httpserver, {DATASET_ID: CHANGED_INFO}))

    assert (summary is not None and summary.other_source, _stored_metadata(db)) == (1, stored)


@pytest.mark.integration
def test_collector_restart_with_stale_snapshot_keeps_refreshed_metadata(db: Database, httpserver: HTTPServer, tmp_path: pathlib.Path) -> None:
    """Reloading a snapshot older than the refresh keeps the refreshed metadata."""
    catalog_file = tmp_path / "master_catalog.json"
    catalog_file.write_text(json.dumps({DATASET_ID: [CATALOG_SOURCE]}))
    metadata_file = tmp_path / "dataset_metadata.json"
    metadata_file.write_text(json.dumps([{"dataset_id": DATASET_ID, "metadata": {**STORED_INFO, "x_server_url": SERVER_URL}}]))
    loader = CatalogLoader(db, catalog_file, metadata_file)
    loader.load_to_db()
    loader.load_metadata_to_db()
    _refresh(db, _serve_mirror(httpserver, {DATASET_ID: CHANGED_INFO}))
    refreshed = _stored_metadata(db)

    loader.load_to_db()
    loader.load_metadata_to_db()

    record, raw_record = _stored_metadata(db)
    assert record is not None and raw_record is not None
    assert (_stored_metadata(db), raw_record.payload) == (refreshed, CHANGED_INFO)
