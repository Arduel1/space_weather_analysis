import pathlib
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, cast

import pytest
import requests
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from catalog_collector.data_catalog.availability_state import AvailabilitySummary
from catalog_collector.data_catalog.hapi_availability import HapiMirrorAvailabilityRefresher
from catalog_collector.data_catalog.snapshot import CatalogSnapshotLoader
from catalog_collector.hapi_mirror.servers import read_server_list
from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.util.error_handling import DataRetrievalError

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

type SaveCatalog = Callable[[dict[str, list[CatalogEntryDict]]], list[MasterCatalogItem]]


def _catalogs(dataset_ids: list[str]) -> dict[str, Any]:
    """Builds catalogs.json with one mirror server listing the datasets, its URL written with http and trailing slash."""
    catalog_entries = [{"id": dataset_id} for dataset_id in dataset_ids]
    return {MIRROR_SERVER_ID: {"about": {"x_url": "http://hapi.example.org/hapi/"}, "catalog": {"catalog": catalog_entries}}}


def _dataset_table(stop_dates: dict[str, str]) -> dict[str, Any]:
    """Builds the dataset table with one row per dataset of the mirror server, each stopping at its given date."""
    body = [[MIRROR_SERVER_ID, dataset_id, "", MIRROR_START, "", stop_date, ""] for dataset_id, stop_date in stop_dates.items()]
    return {"metadata": {"creationDate": MIRROR_CREATED, "columnDefinitions": {}}, "header": TABLE_HEADER, "body": body}


def _serve_mirror(httpserver: HTTPServer, stop_dates: dict[str, str]) -> str:
    """Lets the fake mirror serve both files for the datasets with their stop dates and returns the mirror base URL."""
    httpserver.clear()
    httpserver.expect_request(f"{MIRROR_PATH}/catalogs.json").respond_with_json(_catalogs(list(stop_dates)))
    httpserver.expect_request(f"{MIRROR_PATH}/table/hapi.all.datasets.json").respond_with_json(_dataset_table(stop_dates))
    return httpserver.url_for(f"{MIRROR_PATH}/")


def _source(server_url: str = SERVER_URL, **extra_fields: Any) -> CatalogEntryDict:
    """Builds a HAPI catalog source with the snapshot time range and the extra fields."""
    return cast(CatalogEntryDict, {"type": "HAPI", "source_id": server_url, "time_range": dict(CATALOG_TIME_RANGE), **extra_fields})


def _refresh(
    db: Database, session: requests.Session, mirror_base_url: str, clock: Callable[[], datetime], server_urls: Sequence[str] = ()
) -> AvailabilitySummary:
    """Runs one availability refresh against the mirror and returns its summary."""
    return HapiMirrorAvailabilityRefresher(MongoCatalogRepository(db), session, mirror_base_url, clock, server_urls).refresh()


def _added_source(server_url: str, stop: str, now: datetime) -> dict[str, Any]:
    """Builds the HAPI source the refresh adds for a mirrored dataset of a listed server."""
    return {
        "type": "HAPI",
        "source_id": server_url,
        "time_range": {"start_time": MIRROR_START, "end_time": stop},
        "ongoing": True,
        "availability_checked_at": now,
        "missing_runs": 0,
    }


def _stored_source(db: Database, dataset_id: str = DATASET_ID) -> dict[str, Any]:
    """Reads the single source of a dataset from the catalog."""
    sources = MongoCatalogRepository(db).get_sources(dataset_id)
    assert len(sources) == 1
    return dict(sources[0])


def _failing_table_url(httpserver: HTTPServer, _closed_port_url: str) -> str:
    """Lets the fake mirror serve catalogs.json but answer the dataset table with HTTP 500, and returns the base URL."""
    httpserver.expect_request(f"{MIRROR_PATH}/catalogs.json").respond_with_json(_catalogs([DATASET_ID]))
    httpserver.expect_request(f"{MIRROR_PATH}/table/hapi.all.datasets.json").respond_with_data("Internal Server Error", status=500)
    return httpserver.url_for(f"{MIRROR_PATH}/")


def _refused_url(_httpserver: HTTPServer, closed_port_url: str) -> str:
    """Returns a mirror base URL on a port that refuses the connection."""
    return f"{closed_port_url}{MIRROR_PATH}/"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mirror_stop", "expected_ongoing"),
    [
        pytest.param("2026-10-20T00:00Z", True, id="recent_stop"),
        pytest.param("2017-12-31T23:59Z", False, id="old_stop"),
    ],
)
def test_refresh_sets_mirror_time_range_and_ongoing(
    db: Database,
    httpserver: HTTPServer,
    http_session: requests.Session,
    save_catalog: SaveCatalog,
    clock: Callable[[], datetime],
    now: datetime,
    mirror_stop: str,
    expected_ongoing: bool,
) -> None:
    """A dataset found in the mirror gets its time range, ongoing state and refresh time, and its miss count is cleared."""
    save_catalog({DATASET_ID: [_source(missing_runs=2, missing_since=now)]})

    _refresh(db, http_session, _serve_mirror(httpserver, {DATASET_ID: mirror_stop}), clock)

    assert _stored_source(db) == {
        "type": "HAPI",
        "source_id": SERVER_URL,
        "time_range": {"start_time": MIRROR_START, "end_time": mirror_stop},
        "ongoing": expected_ongoing,
        "availability_checked_at": now,
        "missing_runs": 0,
    }


@pytest.mark.integration
def test_missing_dataset_is_kept_counted_and_reset_on_reappearance(
    db: Database, httpserver: HTTPServer, http_session: requests.Session, save_catalog: SaveCatalog, clock: Callable[[], datetime], now: datetime
) -> None:
    """A dataset absent from its mirrored server stays in the catalog with a growing miss count until it reappears."""
    save_catalog({DATASET_ID: [_source()]})
    without_dataset = {OTHER_DATASET_ID: "2026-10-28T00:00Z"}

    _refresh(db, http_session, _serve_mirror(httpserver, without_dataset), clock)
    first_miss = _stored_source(db)
    _refresh(db, http_session, _serve_mirror(httpserver, without_dataset), clock)
    second_miss = _stored_source(db)
    _refresh(db, http_session, _serve_mirror(httpserver, {**without_dataset, DATASET_ID: "2026-10-28T00:00Z"}), clock)

    assert (first_miss, second_miss) == (
        {**_source(), "missing_runs": 1, "missing_since": now},
        {**_source(), "missing_runs": 2, "missing_since": now},
    )
    assert _stored_source(db) == {
        **_source(),
        "time_range": {"start_time": MIRROR_START, "end_time": "2026-10-28T00:00Z"},
        "ongoing": True,
        "availability_checked_at": now,
        "missing_runs": 0,
    }


@pytest.mark.integration
def test_sources_of_servers_outside_mirror_stay_untouched(
    db: Database, httpserver: HTTPServer, http_session: requests.Session, save_catalog: SaveCatalog, clock: Callable[[], datetime]
) -> None:
    """A source of a server the mirror does not maintain keeps its stored state and is reported as untouched."""
    stored_items = save_catalog({OTHER_DATASET_ID: [_source(UNMIRRORED_SERVER_URL)]})

    summary = _refresh(db, http_session, _serve_mirror(httpserver, {DATASET_ID: "2026-10-28T00:00Z"}), clock)

    assert MongoCatalogRepository(db).get_all() == stored_items
    assert (summary.refreshed, summary.missing, summary.untouched_sources) == (0, 0, [UNMIRRORED_SERVER_URL])


@pytest.mark.integration
def test_collector_restart_with_stale_snapshot_keeps_refreshed_availability(
    db: Database,
    httpserver: HTTPServer,
    http_session: requests.Session,
    snapshot_files: Callable[[dict[str, Any], Any], tuple[Any, Any]],
    clock: Callable[[], datetime],
) -> None:
    """Reloading the stale catalog snapshot keeps the refreshed time range and ongoing state."""
    catalog_file, _ = snapshot_files({DATASET_ID: [_source()]}, [])
    catalog_loader = CatalogSnapshotLoader(MongoCatalogRepository(db), catalog_file)
    catalog_loader.load_to_db()
    _refresh(db, http_session, _serve_mirror(httpserver, {DATASET_ID: "2026-10-28T00:00Z"}), clock)

    catalog_loader.load_to_db()

    after_restart = _stored_source(db)
    assert (after_restart["time_range"], after_restart["ongoing"]) == ({"start_time": MIRROR_START, "end_time": "2026-10-28T00:00Z"}, True)


@pytest.mark.integration
def test_server_listed_under_another_path_of_the_same_host_is_matched_by_dataset(
    db: Database, httpserver: HTTPServer, http_session: requests.Session, save_catalog: SaveCatalog, clock: Callable[[], datetime], now: datetime
) -> None:
    """A catalog server whose path differs from the mirror URL of the same host is refreshed when that mirror server lists the dataset."""
    other_path_url = "https://hapi.example.org/OtherWebApp/hapi"
    save_catalog({DATASET_ID: [_source(other_path_url)]})

    _refresh(db, http_session, _serve_mirror(httpserver, {DATASET_ID: "2026-10-28T00:00Z"}), clock)

    assert _stored_source(db) == {
        **_source(other_path_url),
        "time_range": {"start_time": MIRROR_START, "end_time": "2026-10-28T00:00Z"},
        "ongoing": True,
        "availability_checked_at": now,
        "missing_runs": 0,
    }


@pytest.mark.integration
@pytest.mark.parametrize("mirror_url_of", [pytest.param(_failing_table_url, id="http_500"), pytest.param(_refused_url, id="connection_refused")])
def test_unreachable_mirror_raises_and_leaves_catalog_unchanged(
    db: Database,
    httpserver: HTTPServer,
    http_session: requests.Session,
    save_catalog: SaveCatalog,
    clock: Callable[[], datetime],
    closed_port_url: str,
    mirror_url_of: Callable[[HTTPServer, str], str],
) -> None:
    """A mirror answering with HTTP 500 or refusing the connection raises a retrieval error and changes nothing in the catalog."""
    stored_items = save_catalog({DATASET_ID: [_source()]})

    with pytest.raises(DataRetrievalError):
        _refresh(db, http_session, mirror_url_of(httpserver, closed_port_url), clock)

    assert MongoCatalogRepository(db).get_all() == stored_items


@pytest.mark.integration
def test_empty_catalog_gets_sources_of_listed_servers_only(
    db: Database, httpserver: HTTPServer, http_session: requests.Session, clock: Callable[[], datetime], now: datetime, tmp_path: pathlib.Path
) -> None:
    """Datasets the mirror lists at a server of the server list become catalog items; other servers and undated rows add nothing."""
    server_list_file = tmp_path / "all.txt"
    server_list_file.write_text(f"{SERVER_URL}\n\n", encoding="utf-8")
    catalogs = {
        **_catalogs([DATASET_ID, OTHER_DATASET_ID]),
        "Unlisted": {"about": {"x_url": UNMIRRORED_SERVER_URL}, "catalog": {"catalog": [{"id": "unlisted_P1M"}]}},
    }
    table = _dataset_table({DATASET_ID: "2026-10-28T00:00Z"})
    table["body"] += [
        [MIRROR_SERVER_ID, OTHER_DATASET_ID, "", "", "", "", ""],
        ["Unlisted", "unlisted_P1M", "", MIRROR_START, "", "2026-10-28T00:00Z", ""],
    ]
    httpserver.expect_request(f"{MIRROR_PATH}/catalogs.json").respond_with_json(catalogs)
    httpserver.expect_request(f"{MIRROR_PATH}/table/hapi.all.datasets.json").respond_with_json(table)

    summary = _refresh(db, http_session, httpserver.url_for(f"{MIRROR_PATH}/"), clock, read_server_list(server_list_file))

    assert MongoCatalogRepository(db).get_all() == [{"dataset_id": DATASET_ID, "sources": [_added_source(SERVER_URL, "2026-10-28T00:00Z", now)]}]
    assert (summary.added, summary.undated) == (1, 1)


@pytest.mark.integration
def test_existing_dataset_gains_a_source_of_a_new_server_and_keeps_its_sources(
    db: Database, httpserver: HTTPServer, http_session: requests.Session, save_catalog: SaveCatalog, clock: Callable[[], datetime], now: datetime
) -> None:
    """A dataset held for another server gets the listed server appended, while a dataset already held for the server gets no duplicate."""
    save_catalog({DATASET_ID: [_source(UNMIRRORED_SERVER_URL)], OTHER_DATASET_ID: [_source()]})
    stop = "2026-10-28T00:00Z"

    summary = _refresh(db, http_session, _serve_mirror(httpserver, {DATASET_ID: stop, OTHER_DATASET_ID: stop}), clock, [SERVER_URL])

    repository = MongoCatalogRepository(db)
    assert repository.get_sources(DATASET_ID) == [_source(UNMIRRORED_SERVER_URL), _added_source(SERVER_URL, stop, now)]
    assert repository.get_sources(OTHER_DATASET_ID) == [_added_source(SERVER_URL, stop, now)]
    assert (summary.refreshed, summary.added) == (1, 1)
