from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
import requests
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from catalog_collector.metadata_catalog.hapi_coverage import sync_hapi_coverage
from catalog_collector.metadata_catalog.hapi_metadata import HapiMirrorMetadataRefresher, HapiMirrorMetadataSummary
from catalog_collector.metadata_catalog.snapshot import MetadataSnapshotLoader
from space_weather_analysis.catalog.models import CatalogEntryDict
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSourceKind, RawMetadataRecord, SourceInfo
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.util.error_handling import DataRetrievalError

DATASET_ID = "synthetic_plasma_P1M"
SERVER_URL = "https://hapi.example.org/hapi"
MIRROR_SERVER_ID = "Example"
MIRROR_PATH = "/meta"
STORED_AT = datetime(2026, 7, 21, tzinfo=UTC)
CATALOG_SOURCE: CatalogEntryDict = {
    "type": "HAPI",
    "source_id": SERVER_URL,
    "time_range": {"start_time": "2016-07-26T00:00Z", "end_time": "2026-07-20T00:00Z"},
}

STORED_INFO: dict[str, Any] = {
    "HAPI": "3.1",
    "startDate": "2016-07-26T00:00Z",
    "stopDate": "2026-07-20T00:00Z",
    "parameters": [
        {"name": "Time", "type": "isotime", "units": "UTC", "fill": None, "length": 17},
        {"name": "density", "type": "double", "units": "cm^-3", "fill": "-9999"},
    ],
}
CHANGED_INFO: dict[str, Any] = {
    **STORED_INFO,
    "stopDate": "2026-10-28T00:00Z",
    "parameters": [*STORED_INFO["parameters"], {"name": "speed", "type": "double", "units": "km/s", "fill": "-9999"}],
}


def _mirror_file(dataset_infos: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """Builds catalogs-all.json with one mirror server listing the datasets, each with its info stamped by the mirror fetch time."""
    entries: list[dict[str, Any]] = []
    for dataset_id, info in dataset_infos.items():
        entry: dict[str, Any] = {"id": dataset_id}
        if info is not None:
            entry["info"] = {**info, "x_LastUpdate": "2026-10-29T15:20:00Z"}
        entries.append(entry)
    return {MIRROR_SERVER_ID: {"about": {"x_url": SERVER_URL}, "catalog": {"catalog": entries}}}


def _serve_mirror(httpserver: HTTPServer, dataset_infos: dict[str, dict[str, Any] | None]) -> str:
    """Lets the fake mirror serve its full file with the dataset infos and returns the mirror base URL."""
    httpserver.clear()
    httpserver.expect_request(f"{MIRROR_PATH}/catalogs-all.json").respond_with_json(_mirror_file(dataset_infos))
    return httpserver.url_for(f"{MIRROR_PATH}/")


def _store(db: Database, record: DatasetMetadata | None = None) -> None:
    """Saves the catalog with one HAPI source, the raw info as the HAPI client recorded it, and its record or the given one."""
    MongoCatalogRepository(db).save({"dataset_id": DATASET_ID, "sources": [CATALOG_SOURCE]})
    repository = MongoMetadataRepository(db)
    repository.save_raw(DATASET_ID, MetadataSourceKind.HAPI, {**STORED_INFO, "x_server_url": SERVER_URL}, SERVER_URL, STORED_AT)
    repository.save(record or build_hapi_metadata(DATASET_ID, DATASET_ID, STORED_INFO, SERVER_URL, STORED_AT))


def _stored_metadata(db: Database) -> tuple[DatasetMetadata | None, RawMetadataRecord | None]:
    """Reads the record and the raw HAPI metadata of the dataset."""
    repository = MongoMetadataRepository(db)
    return repository.get(DATASET_ID), repository.get_raw(DATASET_ID, MetadataSourceKind.HAPI)


def _refresh(db: Database, session: requests.Session, mirror_base_url: str, clock: Callable[[], datetime]) -> HapiMirrorMetadataSummary:
    """Runs one metadata refresh against the mirror and returns its summary."""
    return HapiMirrorMetadataRefresher(MongoCatalogRepository(db), MongoMetadataRepository(db), session, mirror_base_url, clock).refresh()


@pytest.mark.integration
def test_changed_info_replaces_raw_metadata_and_record(
    db: Database, httpserver: HTTPServer, http_session: requests.Session, clock: Callable[[], datetime], now: datetime
) -> None:
    """A mirrored info that differs from the stored one is stored without the mirror's fetch time and normalized again."""
    _store(db)

    summary = _refresh(db, http_session, _serve_mirror(httpserver, {DATASET_ID: CHANGED_INFO}), clock)

    record, raw_record = _stored_metadata(db)
    expected_record = build_hapi_metadata(DATASET_ID, DATASET_ID, CHANGED_INFO, SERVER_URL, now)
    assert (summary.replaced, record, raw_record is not None and (raw_record.payload, raw_record.retrieved_at)) == (
        1,
        expected_record,
        (CHANGED_INFO, now),
    )


@pytest.mark.integration
def test_info_differing_only_in_volatile_keys_is_not_rewritten(
    db: Database, httpserver: HTTPServer, http_session: requests.Session, clock: Callable[[], datetime]
) -> None:
    """An info that differs only in the mirror's fetch time and the recorded server URL leaves the stored metadata untouched."""
    _store(db)
    stored = _stored_metadata(db)

    summary = _refresh(db, http_session, _serve_mirror(httpserver, {DATASET_ID: STORED_INFO}), clock)

    assert (summary.unchanged, _stored_metadata(db)) == (1, stored)


@pytest.mark.integration
@pytest.mark.parametrize(
    "dataset_infos",
    [
        pytest.param({"synthetic_other_P1M": STORED_INFO}, id="dataset_missing"),
        pytest.param({DATASET_ID: None}, id="listed_without_info"),
        pytest.param({DATASET_ID: {**CHANGED_INFO, "parameters": []}}, id="info_not_normalizable"),
    ],
)
def test_unusable_mirror_entry_keeps_last_good_metadata(
    db: Database, httpserver: HTTPServer, http_session: requests.Session, clock: Callable[[], datetime], dataset_infos: dict[str, Any]
) -> None:
    """A dataset absent from the mirror, listed without info, or with an info lacking parameters keeps the stored metadata."""
    _store(db)
    stored = _stored_metadata(db)

    _refresh(db, http_session, _serve_mirror(httpserver, dataset_infos), clock)

    assert _stored_metadata(db) == stored


@pytest.mark.integration
def test_unreadable_mirror_raises_and_keeps_last_good_metadata(
    db: Database, httpserver: HTTPServer, http_session: requests.Session, clock: Callable[[], datetime]
) -> None:
    """A mirror answering with HTTP 500 raises a retrieval error and changes no metadata."""
    _store(db)
    stored = _stored_metadata(db)
    httpserver.expect_request(f"{MIRROR_PATH}/catalogs-all.json").respond_with_data("Internal Server Error", status=500)

    with pytest.raises(DataRetrievalError):
        _refresh(db, http_session, httpserver.url_for(f"{MIRROR_PATH}/"), clock)

    assert _stored_metadata(db) == stored


@pytest.mark.integration
def test_record_of_another_source_kind_is_not_replaced(
    db: Database, httpserver: HTTPServer, http_session: requests.Session, clock: Callable[[], datetime]
) -> None:
    """A record built from another source kind ranks above HAPI metadata and stays in place."""
    _store(db, record=DatasetMetadata(dataset_id=DATASET_ID, collection=DATASET_ID, source=SourceInfo(type="CDAWEB")))
    stored = _stored_metadata(db)

    summary = _refresh(db, http_session, _serve_mirror(httpserver, {DATASET_ID: CHANGED_INFO}), clock)

    assert (summary.other_source, _stored_metadata(db)) == (1, stored)


@pytest.mark.integration
def test_collector_restart_with_stale_snapshot_keeps_refreshed_metadata(
    db: Database,
    httpserver: HTTPServer,
    http_session: requests.Session,
    snapshot_files: Callable[[dict[str, Any], Any], tuple[Any, Any]],
    clock: Callable[[], datetime],
) -> None:
    """Reloading the snapshot after a refresh keeps the refreshed metadata."""
    _, metadata_file = snapshot_files({}, [{"dataset_id": DATASET_ID, "metadata": {**STORED_INFO, "x_server_url": SERVER_URL}}])
    MongoCatalogRepository(db).save({"dataset_id": DATASET_ID, "sources": [CATALOG_SOURCE]})
    loader = MetadataSnapshotLoader(MongoCatalogRepository(db), MongoMetadataRepository(db), metadata_file, clock)
    loader.load_metadata_to_db()
    _refresh(db, http_session, _serve_mirror(httpserver, {DATASET_ID: CHANGED_INFO}), clock)
    refreshed = _stored_metadata(db)

    loader.load_metadata_to_db()

    assert _stored_metadata(db) == refreshed


@pytest.mark.integration
def test_coverage_follows_the_refreshed_time_range_of_the_record_server(db: Database, now: datetime) -> None:
    """A HAPI record takes the time range the availability refresh stored for its server, while a record of another source keeps its own."""
    refreshed_source: CatalogEntryDict = {
        **CATALOG_SOURCE,
        "time_range": {"start_time": "2016-07-26T00:00Z", "end_time": "2026-10-28T00:00Z"},
        "availability_checked_at": now,
    }
    MongoCatalogRepository(db).save_many(
        [{"dataset_id": DATASET_ID, "sources": [refreshed_source]}, {"dataset_id": "synthetic_cdaweb", "sources": [{**refreshed_source}]}]
    )
    repository = MongoMetadataRepository(db)
    repository.save(build_hapi_metadata(DATASET_ID, DATASET_ID, STORED_INFO, SERVER_URL, STORED_AT))
    repository.save(DatasetMetadata(dataset_id="synthetic_cdaweb", collection="synthetic_cdaweb", source=SourceInfo(type="CDAWEB")))
    other_record = repository.get("synthetic_cdaweb")

    changed = sync_hapi_coverage(MongoCatalogRepository(db), repository)

    record = repository.get(DATASET_ID)
    assert (changed, record is not None and record.time.coverage_stop, repository.get("synthetic_cdaweb")) == (
        1,
        datetime(2026, 10, 28, tzinfo=UTC),
        other_record,
    )
