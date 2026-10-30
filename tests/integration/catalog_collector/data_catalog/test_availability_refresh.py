import json
import pathlib
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, cast

import pytest
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from catalog_collector.data_catalog.hapi_availability import AvailabilityRefresher, AvailabilitySummary
from catalog_collector.data_catalog.snapshot import CatalogSnapshotLoader
from catalog_collector.metadata_catalog.snapshot import MetadataSnapshotLoader
from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.models import DatasetMetadata, SourceInfo, TimeInfo
from space_weather_analysis.metadata.repository import MongoMetadataRepository

DATASET_ID = "synthetic_plasma_P1M"
OTHER_DATASET_ID = "synthetic_mag_P1M"
SERVER_URL = "https://hapi.example.org/hapi"
UNMIRRORED_SERVER_URL = "https://unmirrored.example.org/hapi"
MIRROR_SERVER_ID = "Example"
MIRROR_PATH = "/meta"

# The mirror generated its files at this time; ongoing is decided relative to it, not to the test run.
MIRROR_CREATED = "2026-10-29T16:07:53Z"
CATALOG_TIME_RANGE = {"start_time": "2016-07-26T00:00:00Z", "end_time": "2026-01-11T01:59:00Z"}
MIRROR_START = "2016-07-26T00:00Z"

# Header of the mirror dataset table, reduced to the columns the refresh reads.
TABLE_HEADER = ["server", "dataset", "title", "startDate", "x_startDate", "stopDate", "x_stopDate"]


def _catalogs(dataset_ids: List[str]) -> Dict[str, Any]:
    """Builds catalogs.json with one mirror server listing the datasets, its URL written with http and trailing slash."""
    catalog_entries = [{"id": dataset_id} for dataset_id in dataset_ids]
    return {MIRROR_SERVER_ID: {"about": {"x_url": "http://hapi.example.org/hapi/"}, "catalog": {"catalog": catalog_entries}}}


def _dataset_table(stop_dates: Dict[str, str]) -> Dict[str, Any]:
    """Builds the dataset table with one row per dataset of the mirror server, each stopping at its given date."""
    body = [[MIRROR_SERVER_ID, dataset_id, "", MIRROR_START, "", stop_date, ""] for dataset_id, stop_date in stop_dates.items()]
    return {"metadata": {"creationDate": MIRROR_CREATED, "columnDefinitions": {}}, "header": TABLE_HEADER, "body": body}


def _serve_mirror(httpserver: HTTPServer, stop_dates: Dict[str, str]) -> str:
    """Lets the fake mirror serve both files for the datasets with their stop dates and returns the mirror base URL."""
    httpserver.clear()
    httpserver.expect_request(f"{MIRROR_PATH}/catalogs.json").respond_with_json(_catalogs(list(stop_dates)))
    httpserver.expect_request(f"{MIRROR_PATH}/table/hapi.all.datasets.json").respond_with_json(_dataset_table(stop_dates))
    return httpserver.url_for(f"{MIRROR_PATH}/")


def _source(server_url: str = SERVER_URL, **extra_fields: Any) -> CatalogEntryDict:
    """Builds a HAPI catalog source with the snapshot time range and the extra fields."""
    return cast(CatalogEntryDict, {"type": "HAPI", "source_id": server_url, "time_range": dict(CATALOG_TIME_RANGE), **extra_fields})


def _save_catalog(db: Database, items: Dict[str, List[CatalogEntryDict]]) -> List[MasterCatalogItem]:
    """Saves catalog items given as sources per dataset ID and returns them as stored."""
    catalog_items: List[MasterCatalogItem] = []
    for dataset_id, sources in items.items():
        catalog_items.append({"dataset_id": dataset_id, "sources": sources})
    MongoCatalogRepository(db).save_many(catalog_items)
    return MongoCatalogRepository(db).get_all()


def _refresh(db: Database, mirror_base_url: str) -> Optional[AvailabilitySummary]:
    """Runs one availability refresh against the mirror and returns its summary."""
    return AvailabilityRefresher(MongoCatalogRepository(db), MongoMetadataRepository(db), mirror_base_url).refresh()


def _stored_source(db: Database, dataset_id: str = DATASET_ID) -> Dict[str, Any]:
    """Reads the single source of a dataset from the catalog."""
    sources = MongoCatalogRepository(db).get_sources(dataset_id)
    assert len(sources) == 1
    return dict(sources[0])


def _utc_now_text() -> str:
    """Returns the current UTC time in the text format of the refresh times."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mirror_stop", "expected_ongoing"),
    [
        pytest.param("2026-10-20T00:00Z", True, id="recent_stop"),
        pytest.param("2017-12-31T23:59Z", False, id="old_stop"),
    ],
)
def test_refresh_sets_mirror_time_range_and_ongoing(db: Database, httpserver: HTTPServer, mirror_stop: str, expected_ongoing: bool) -> None:
    """A dataset found in the mirror gets its time range and ongoing state from it, and the metadata coverage follows."""
    _save_catalog(db, {DATASET_ID: [_source(missing_runs=2, missing_since="2026-10-01T00:00:00Z")]})
    stale_time = TimeInfo(coverage_start=datetime(2016, 7, 26, tzinfo=timezone.utc), coverage_stop=datetime(2026, 1, 11, tzinfo=timezone.utc))
    MongoMetadataRepository(db).save(
        DatasetMetadata(dataset_id=DATASET_ID, collection=DATASET_ID, source=SourceInfo(type="HAPI", server_url=SERVER_URL), time=stale_time)
    )
    refresh_started = _utc_now_text()

    _refresh(db, _serve_mirror(httpserver, {DATASET_ID: mirror_stop}))

    source = _stored_source(db)
    assert refresh_started <= source.pop("availability_checked_at") <= _utc_now_text()
    assert source == {
        "type": "HAPI",
        "source_id": SERVER_URL,
        "time_range": {"start_time": MIRROR_START, "end_time": mirror_stop},
        "ongoing": expected_ongoing,
        "missing_runs": 0,
    }
    record = MongoMetadataRepository(db).get(DATASET_ID)
    assert record is not None
    assert (record.time.coverage_start, record.time.coverage_stop) == (
        datetime.fromisoformat(MIRROR_START),
        datetime.fromisoformat(mirror_stop),
    )


@pytest.mark.integration
def test_missing_dataset_is_kept_counted_and_reset_on_reappearance(db: Database, httpserver: HTTPServer) -> None:
    """A dataset absent from its mirrored server stays in the catalog with a growing miss count until it reappears."""
    _save_catalog(db, {DATASET_ID: [_source()]})
    without_dataset = {OTHER_DATASET_ID: "2026-10-28T00:00Z"}

    _refresh(db, _serve_mirror(httpserver, without_dataset))
    first_miss = _stored_source(db)
    _refresh(db, _serve_mirror(httpserver, without_dataset))
    second_miss = _stored_source(db)
    _refresh(db, _serve_mirror(httpserver, {**without_dataset, DATASET_ID: "2026-10-28T00:00Z"}))
    reappeared = _stored_source(db)

    missing_since = first_miss["missing_since"]
    assert (first_miss, second_miss) == (
        {**_source(), "missing_runs": 1, "missing_since": missing_since},
        {**_source(), "missing_runs": 2, "missing_since": missing_since},
    )
    reappeared.pop("availability_checked_at")
    assert reappeared == {
        **_source(),
        "time_range": {"start_time": MIRROR_START, "end_time": "2026-10-28T00:00Z"},
        "ongoing": True,
        "missing_runs": 0,
    }


@pytest.mark.integration
def test_sources_of_servers_outside_mirror_stay_untouched(db: Database, httpserver: HTTPServer) -> None:
    """A source of a server the mirror does not maintain keeps its stored state and is reported as untouched."""
    stored_items = _save_catalog(db, {OTHER_DATASET_ID: [_source(UNMIRRORED_SERVER_URL)]})

    summary = _refresh(db, _serve_mirror(httpserver, {DATASET_ID: "2026-10-28T00:00Z"}))

    assert MongoCatalogRepository(db).get_all() == stored_items
    assert summary is not None
    assert (summary.refreshed, summary.missing, summary.untouched_servers) == (0, 0, [UNMIRRORED_SERVER_URL])


@pytest.mark.integration
def test_collector_restart_with_stale_snapshot_keeps_refreshed_availability(db: Database, httpserver: HTTPServer, tmp_path: pathlib.Path) -> None:
    """Reloading the stale snapshot keeps the refreshed time range, and the next refresh restores the metadata coverage."""
    catalog_file = tmp_path / "master_catalog.json"
    catalog_file.write_text(json.dumps({DATASET_ID: [_source()]}))
    metadata_file = tmp_path / "dataset_metadata.json"
    snapshot_info = {"startDate": "2016-07-26T00:00:00Z", "stopDate": "2026-01-11T01:59:00Z", "parameters": [{"name": "Time", "type": "isotime"}]}
    metadata_file.write_text(json.dumps([{"dataset_id": DATASET_ID, "metadata": {**snapshot_info, "x_server_url": SERVER_URL}}]))
    catalog_loader = CatalogSnapshotLoader(db, catalog_file)
    metadata_loader = MetadataSnapshotLoader(db, metadata_file)
    catalog_loader.load_to_db()
    metadata_loader.load_metadata_to_db()
    mirror_base_url = _serve_mirror(httpserver, {DATASET_ID: "2026-10-28T00:00Z"})
    _refresh(db, mirror_base_url)

    catalog_loader.load_to_db()
    metadata_loader.load_metadata_to_db()
    after_restart = _stored_source(db)
    _refresh(db, mirror_base_url)

    assert (after_restart["time_range"], after_restart["ongoing"]) == ({"start_time": MIRROR_START, "end_time": "2026-10-28T00:00Z"}, True)
    record = MongoMetadataRepository(db).get(DATASET_ID)
    assert record is not None
    assert record.time.coverage_stop == datetime.fromisoformat("2026-10-28T00:00Z")


@pytest.mark.integration
def test_server_listed_under_another_path_of_the_same_host_is_matched_by_dataset(db: Database, httpserver: HTTPServer) -> None:
    """A catalog server whose path differs from the mirror URL of the same host is refreshed when that mirror server lists the dataset."""
    other_path_url = "https://hapi.example.org/OtherWebApp/hapi"
    _save_catalog(db, {DATASET_ID: [_source(other_path_url)]})

    _refresh(db, _serve_mirror(httpserver, {DATASET_ID: "2026-10-28T00:00Z"}))

    source = _stored_source(db)
    source.pop("availability_checked_at")
    assert source == {
        **_source(other_path_url),
        "time_range": {"start_time": MIRROR_START, "end_time": "2026-10-28T00:00Z"},
        "ongoing": True,
        "missing_runs": 0,
    }


def _closed_port_url() -> str:
    """Returns a local mirror base URL on a port nobody listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}{MIRROR_PATH}/"


def _failing_table_url(httpserver: HTTPServer) -> str:
    """Lets the fake mirror serve catalogs.json but answer the dataset table with HTTP 500, and returns the base URL."""
    httpserver.expect_request(f"{MIRROR_PATH}/catalogs.json").respond_with_json(_catalogs([DATASET_ID]))
    httpserver.expect_request(f"{MIRROR_PATH}/table/hapi.all.datasets.json").respond_with_data("Internal Server Error", status=500)
    return httpserver.url_for(f"{MIRROR_PATH}/")


@pytest.mark.integration
@pytest.mark.parametrize(
    "unreachable",
    [
        pytest.param("http_500", id="http_500"),
        pytest.param("connection_refused", id="connection_refused"),
    ],
)
def test_unreachable_mirror_leaves_catalog_unchanged(db: Database, httpserver: HTTPServer, unreachable: str) -> None:
    """A mirror answering with HTTP 500 or refusing the connection changes nothing in the catalog."""
    stored_items = _save_catalog(db, {DATASET_ID: [_source()]})
    mirror_base_url = _failing_table_url(httpserver) if unreachable == "http_500" else _closed_port_url()

    summary = _refresh(db, mirror_base_url)

    assert (summary, MongoCatalogRepository(db).get_all()) == (None, stored_items)
