from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
import requests
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from catalog_collector.cdaweb.http import CdawebRequestError
from catalog_collector.data_catalog.cdas_availability import CdasAvailabilityRefresher, CdasAvailabilitySummary
from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem
from space_weather_analysis.catalog.repository import MongoCatalogRepository

CDAS_PATH = "/WS/cdasr/1/dataviews/sp_phys/datasets"
DATASET_ID = "SYNTH_K0_FIDO"
CATALOG_TIME_RANGE = {"start_time": "2016-07-26T00:00:00Z", "end_time": "2026-01-11T00:00:00Z"}
CDAS_START = "2016-07-26T00:00:00.000Z"


def _source() -> CatalogEntryDict:
    """Builds a FIDO_CDAWEB catalog source with the snapshot time range."""
    return cast(CatalogEntryDict, {"type": "FIDO_CDAWEB", "source_id": "SYNTH", "time_range": dict(CATALOG_TIME_RANGE)})


def _serve_cdas(httpserver: HTTPServer, stop_dates: dict[str, str]) -> str:
    """Lets the fake CDAS list the datasets with their stop dates and returns the list URL."""
    httpserver.clear()
    entries = [{"Id": dataset_id, "TimeInterval": {"Start": CDAS_START, "End": stop}} for dataset_id, stop in stop_dates.items()]
    httpserver.expect_request(CDAS_PATH).respond_with_json({"DatasetDescription": entries})
    return httpserver.url_for(CDAS_PATH)


def _refresh(db: Database, url: str) -> CdasAvailabilitySummary:
    """Runs one CDAS availability refresh and returns its summary."""
    return CdasAvailabilityRefresher(MongoCatalogRepository(db), requests.Session(), url).refresh()


def _save_catalog(db: Database) -> list[MasterCatalogItem]:
    """Saves the catalog with one FIDO_CDAWEB source and returns it as stored."""
    MongoCatalogRepository(db).save({"dataset_id": DATASET_ID, "sources": [_source()]})
    return MongoCatalogRepository(db).get_all()


def _stored_source(db: Database) -> dict[str, Any]:
    """Reads the single source of the dataset without its refresh time."""
    source = dict(MongoCatalogRepository(db).get_sources(DATASET_ID)[0])
    source.pop("availability_checked_at", None)
    return source


def _days_ago(days: int) -> str:
    """Returns the time the given number of days before now in the CDAS time format."""
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.mark.integration
@pytest.mark.parametrize(("stop_days_ago", "expected_ongoing"), [pytest.param(5, True, id="recent_stop"), pytest.param(3000, False, id="old_stop")])
def test_listed_dataset_gets_cdas_time_range_and_ongoing(db: Database, httpserver: HTTPServer, stop_days_ago: int, expected_ongoing: bool) -> None:
    """A dataset CDAS lists gets its time range from CDAS and counts as ongoing when it stopped within the tolerance."""
    _save_catalog(db)
    stop = _days_ago(stop_days_ago)

    _refresh(db, _serve_cdas(httpserver, {DATASET_ID: stop}))

    assert _stored_source(db) == {
        **_source(),
        "time_range": {"start_time": CDAS_START, "end_time": stop},
        "ongoing": expected_ongoing,
        "missing_runs": 0,
    }


@pytest.mark.integration
def test_unlisted_dataset_is_kept_counted_and_reset_on_reappearance(db: Database, httpserver: HTTPServer) -> None:
    """A dataset CDAS does not list keeps its state with a growing miss count until CDAS lists it again."""
    _save_catalog(db)
    without_dataset = _serve_cdas(httpserver, {"SYNTH_OTHER": _days_ago(5)})

    _refresh(db, without_dataset)
    second = _refresh(db, without_dataset)
    missing = _stored_source(db)
    stop = _days_ago(5)
    _refresh(db, _serve_cdas(httpserver, {DATASET_ID: stop}))

    assert (second.missing, missing) == (1, {**_source(), "missing_runs": 2, "missing_since": missing["missing_since"]})
    assert _stored_source(db) == {**_source(), "time_range": {"start_time": CDAS_START, "end_time": stop}, "ongoing": True, "missing_runs": 0}


@pytest.mark.integration
def test_overloaded_cdas_leaves_catalog_unchanged_and_names_the_retry_after(db: Database, httpserver: HTTPServer) -> None:
    """HTTP 429 with Retry-After changes nothing in the catalog and raises an error carrying the requested wait."""
    stored = _save_catalog(db)
    httpserver.expect_request(CDAS_PATH).respond_with_data("Too Many Requests", status=429, headers={"Retry-After": "120"})

    with pytest.raises(CdawebRequestError) as raised:
        _refresh(db, httpserver.url_for(CDAS_PATH))

    assert (raised.value.retry_after_seconds, MongoCatalogRepository(db).get_all()) == (120.0, stored)
