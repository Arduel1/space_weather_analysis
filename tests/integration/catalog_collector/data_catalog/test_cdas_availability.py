from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast

import pytest
import requests
from pymongo.database import Database

from catalog_collector.cdaweb.datasets import fetch_cdas_datasets
from catalog_collector.data_catalog.availability_state import AvailabilitySummary
from catalog_collector.data_catalog.cdas_availability import CdasAvailabilityRefresher
from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem
from space_weather_analysis.catalog.repository import MongoCatalogRepository

DATASET_ID = "SYNTH_K0_CDAWEB"
CATALOG_TIME_RANGE = {"start_time": "2016-07-26T00:00:00Z", "end_time": "2026-01-11T00:00:00Z"}
CDAS_START = "2016-07-26T00:00:00.000Z"


def _source() -> CatalogEntryDict:
    """Builds a CDAWEB catalog source with the snapshot time range."""
    return cast(CatalogEntryDict, {"type": "CDAWEB", "source_id": "SYNTH", "time_range": dict(CATALOG_TIME_RANGE)})


def _entries(stop_dates: dict[str, str]) -> list[dict[str, Any]]:
    """Builds CDAS entries for the datasets with their stop dates."""
    return [{"Id": dataset_id, "TimeInterval": {"Start": CDAS_START, "End": stop}} for dataset_id, stop in stop_dates.items()]


def _refresh(db: Database, session: requests.Session, url: str, clock: Callable[[], datetime]) -> AvailabilitySummary:
    """Reads the CDAS dataset list and runs one availability refresh with it."""
    return CdasAvailabilityRefresher(MongoCatalogRepository(db), clock).refresh(fetch_cdas_datasets(session, url))


def _stored_source(db: Database) -> dict[str, Any]:
    """Reads the single source of the dataset."""
    return dict(MongoCatalogRepository(db).get_sources(DATASET_ID)[0])


def _cdas_time(time: datetime) -> str:
    """Formats a time in the CDAS time format."""
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.mark.integration
@pytest.mark.parametrize(("stop_days_ago", "expected_ongoing"), [pytest.param(5, True, id="recent_stop"), pytest.param(3000, False, id="old_stop")])
def test_listed_dataset_gets_cdas_time_range_and_ongoing(
    db: Database,
    http_session: requests.Session,
    serve_cdas: Callable[[list[dict[str, Any]]], str],
    save_catalog: Callable[[dict[str, list[CatalogEntryDict]]], list[MasterCatalogItem]],
    clock: Callable[[], datetime],
    now: datetime,
    stop_days_ago: int,
    expected_ongoing: bool,
) -> None:
    """A dataset CDAS lists gets its time range from CDAS and counts as ongoing when it stopped within the tolerance."""
    save_catalog({DATASET_ID: [_source()]})
    stop = _cdas_time(now - timedelta(days=stop_days_ago))

    _refresh(db, http_session, serve_cdas(_entries({DATASET_ID: stop})), clock)

    assert _stored_source(db) == {
        **_source(),
        "time_range": {"start_time": CDAS_START, "end_time": stop},
        "ongoing": expected_ongoing,
        "availability_checked_at": now,
        "missing_runs": 0,
    }


@pytest.mark.integration
def test_unlisted_dataset_is_kept_counted_and_reset_on_reappearance(
    db: Database,
    http_session: requests.Session,
    serve_cdas: Callable[[list[dict[str, Any]]], str],
    save_catalog: Callable[[dict[str, list[CatalogEntryDict]]], list[MasterCatalogItem]],
    clock: Callable[[], datetime],
    now: datetime,
) -> None:
    """A dataset CDAS does not list keeps its state with a growing miss count until CDAS lists it again."""
    save_catalog({DATASET_ID: [_source()]})
    stop = _cdas_time(now - timedelta(days=5))
    without_dataset = serve_cdas(_entries({"SYNTH_OTHER": stop}))

    _refresh(db, http_session, without_dataset, clock)
    second = _refresh(db, http_session, without_dataset, clock)
    missing = _stored_source(db)
    _refresh(db, http_session, serve_cdas(_entries({DATASET_ID: stop})), clock)

    assert (second.missing, missing) == (1, {**_source(), "missing_runs": 2, "missing_since": now})
    assert _stored_source(db) == {
        **_source(),
        "time_range": {"start_time": CDAS_START, "end_time": stop},
        "ongoing": True,
        "availability_checked_at": now,
        "missing_runs": 0,
    }


@pytest.mark.integration
def test_unknown_datasets_get_a_cdaweb_source_of_their_observatory_group(
    db: Database,
    http_session: requests.Session,
    serve_cdas: Callable[[list[dict[str, Any]]], str],
    save_catalog: Callable[[dict[str, list[CatalogEntryDict]]], list[MasterCatalogItem]],
    clock: Callable[[], datetime],
    now: datetime,
) -> None:
    """A listed dataset without CDAWEB source gets one, appended to an existing item; an entry without time range adds nothing."""
    hapi_source = cast(CatalogEntryDict, {"type": "HAPI", "source_id": "https://cdaweb.gsfc.nasa.gov/hapi", "time_range": dict(CATALOG_TIME_RANGE)})
    save_catalog({DATASET_ID: [hapi_source]})
    stop = _cdas_time(now - timedelta(days=5))
    entries = [
        {**_entries({DATASET_ID: stop})[0], "ObservatoryGroup": ["SYNTH", "OTHER"]},
        {**_entries({"SYNTH_NEW": stop})[0], "ObservatoryGroup": ["NEW"]},
        {"Id": "SYNTH_UNDATED", "ObservatoryGroup": ["NEW"]},
    ]

    summary = _refresh(db, http_session, serve_cdas(entries), clock)

    def added(group: str) -> dict[str, Any]:
        """Builds the CDAWEB source the refresh adds for a dataset of the group."""
        return {
            "type": "CDAWEB",
            "source_id": group,
            "time_range": {"start_time": CDAS_START, "end_time": stop},
            "ongoing": True,
            "availability_checked_at": now,
            "missing_runs": 0,
        }

    assert MongoCatalogRepository(db).get_all() == [
        {"dataset_id": DATASET_ID, "sources": [hapi_source, added("SYNTH")]},
        {"dataset_id": "SYNTH_NEW", "sources": [added("NEW")]},
    ]
    assert (summary.added, summary.undated) == (2, 1)
