from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from catalog_collector.scheduling.models import MetadataTask
from catalog_collector.worker_tasks.cdas_dataset_list import CDAS_DATASET_LIST_KIND, cdas_dataset_list_kind
from catalog_collector.worker_tasks.settings import TaskIntervalSettings
from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem, TimeRangeDict
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.util.error_handling import DataRetrievalError

DATASET_ID = "SYNTH_K0_FIDO"
CDAS_PATH = "/WS/cdasr/1/dataviews/sp_phys/datasets"
TIME_RANGE: TimeRangeDict = {"start_time": "2016-07-26T00:00:00Z", "end_time": "2026-01-11T00:00:00Z"}
CDAS_ENTRY: dict[str, Any] = {"Id": DATASET_ID, "TimeInterval": {"Start": "2016-07-26T00:00:00.000Z", "End": "2026-10-01T00:00:00.000Z"}}

type SaveCatalog = Callable[[dict[str, list[CatalogEntryDict]]], list[MasterCatalogItem]]


def _task(url: str, now: datetime) -> MetadataTask:
    """Builds the single task of the CDAS dataset list kind."""
    return MetadataTask(
        id=f"{CDAS_DATASET_LIST_KIND}/all", kind=CDAS_DATASET_LIST_KIND, target="all", url=url, interval_seconds=86400, next_due_at=now
    )


@pytest.mark.integration
def test_one_read_of_the_dataset_list_refreshes_availability_and_metadata(
    db: Database, httpserver: HTTPServer, serve_cdas: Callable[[list[dict[str, Any]]], str], save_catalog: SaveCatalog, now: datetime
) -> None:
    """The dataset list is requested once per run and sets both the CDAWeb time range and the stored CDAS entry."""
    save_catalog({DATASET_ID: [{"type": "FIDO_CDAWEB", "source_id": "SYNTH", "time_range": TIME_RANGE}]})
    url = serve_cdas([CDAS_ENTRY])
    kind = cdas_dataset_list_kind(db, TaskIntervalSettings(), url)

    kind.run(_task(url, now))

    source = MongoCatalogRepository(db).get_sources(DATASET_ID)[0]
    raw_entry = MongoMetadataRepository(db).get_raw(DATASET_ID, "cdas")
    assert (len(httpserver.log), source["time_range"]["end_time"], raw_entry is not None and raw_entry.payload) == (
        1,
        "2026-10-01T00:00:00.000Z",
        CDAS_ENTRY,
    )


@pytest.mark.integration
def test_overloaded_cdas_changes_nothing_and_names_the_retry_after(
    db: Database, httpserver: HTTPServer, save_catalog: SaveCatalog, now: datetime
) -> None:
    """HTTP 429 with Retry-After raises an error carrying the requested wait and leaves catalog and metadata unchanged."""
    stored = save_catalog({DATASET_ID: [{"type": "FIDO_CDAWEB", "source_id": "SYNTH", "time_range": TIME_RANGE}]})
    httpserver.expect_request(CDAS_PATH).respond_with_data("Too Many Requests", status=429, headers={"Retry-After": "120"})
    url = httpserver.url_for(CDAS_PATH)

    with pytest.raises(DataRetrievalError) as raised:
        cdas_dataset_list_kind(db, TaskIntervalSettings(), url).run(_task(url, now))

    assert (raised.value.retry_after_seconds, MongoCatalogRepository(db).get_all(), MongoMetadataRepository(db).get_raw(DATASET_ID, "cdas")) == (
        120.0,
        stored,
        None,
    )
