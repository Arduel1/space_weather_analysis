import json
import pathlib
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import pytest
from pymongo.database import Database

from catalog_collector.data_catalog.discovery.builder import CatalogBuilder
from catalog_collector.data_catalog.discovery.discoverers.base import AbstractDiscoverer, DiscoveryYield
from catalog_collector.data_catalog.snapshot import CatalogSnapshotLoader
from catalog_collector.metadata_catalog.snapshot import MetadataSnapshotLoader
from space_weather_analysis.catalog.models import CatalogEntryDict, SourceType
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.util.error_handling import DataRetrievalError

SERVER_A = "https://a.example.org/hapi"
SERVER_B = "https://b.example.org/hapi"
TIME_RANGE = {"start_time": "2020-01-01T00:00Z", "end_time": "2020-02-01T00:00Z"}


def _entry(server_url: str) -> CatalogEntryDict:
    """Builds a HAPI catalog entry of a server with a fixed time range."""
    return {"type": SourceType.HAPI, "source_id": server_url, "time_range": dict(TIME_RANGE)}  # type: ignore[typeddict-item]


class FakeHapiDiscoverer(AbstractDiscoverer):
    """A HAPI discoverer that yields fixed entries and reports fixed failed servers, standing in for the remote servers."""

    source_type = SourceType.HAPI

    def __init__(self, entries: list[tuple[str, str]], failed_servers: set[str], error: DataRetrievalError | None = None) -> None:
        """Keeps the entries as (dataset ID, server URL), the servers to report as failed and an error to raise instead."""
        super().__init__()
        self.entries = entries
        self.failed_servers = failed_servers
        self.error = error

    def discover(self) -> Iterator[DiscoveryYield]:
        """Yields the fixed entries without metadata after recording the failed servers, or raises the error."""
        if self.error is not None:
            raise self.error
        self.failed_source_ids.update(self.failed_servers)
        for dataset_id, server_url in self.entries:
            yield dataset_id, _entry(server_url), None


def _build(db: Database, tmp_path: pathlib.Path, discoverer: AbstractDiscoverer, clock: Callable[[], datetime]) -> pathlib.Path:
    """Runs a catalog build with one discoverer and returns the written catalog file."""
    catalog_repository = MongoCatalogRepository(db)
    catalog_file = tmp_path / "master_catalog.json"
    builder = CatalogBuilder(
        [discoverer],
        catalog_file,
        catalog_repository,
        CatalogSnapshotLoader(catalog_repository, catalog_file),
        MetadataSnapshotLoader(catalog_repository, MongoMetadataRepository(db), tmp_path / "unused.json", clock),
        clock,
    )
    builder.build_catalog()
    return catalog_file


def _stored_sources(db: Database) -> dict[str, list[dict[str, Any]]]:
    """Reads every stored dataset with its sources."""
    return {item["dataset_id"]: [dict(source) for source in item["sources"]] for item in MongoCatalogRepository(db).get_all()}


@pytest.mark.integration
def test_unlisted_source_is_counted_missing_and_a_failed_server_untouched(
    db: Database, tmp_path: pathlib.Path, clock: Callable[[], datetime], now: datetime
) -> None:
    """A source the discoverer no longer lists is kept with a miss count, while the sources of a server it could not read stay unchanged."""
    MongoCatalogRepository(db).save_many(
        [
            {"dataset_id": "kept", "sources": [_entry(SERVER_A)]},
            {"dataset_id": "gone", "sources": [_entry(SERVER_A)]},
            {"dataset_id": "unreadable", "sources": [_entry(SERVER_B)]},
        ]
    )

    catalog_file = _build(db, tmp_path, FakeHapiDiscoverer([("kept", SERVER_A), ("new", SERVER_A)], {SERVER_B}), clock)

    assert _stored_sources(db) == {
        "kept": [dict(_entry(SERVER_A))],
        "gone": [{**_entry(SERVER_A), "missing_runs": 1, "missing_since": now}],
        "unreadable": [dict(_entry(SERVER_B))],
        "new": [dict(_entry(SERVER_A))],
    }
    assert sorted(json.loads(catalog_file.read_text())) == ["kept", "new"]


@pytest.mark.integration
def test_failed_discoverer_changes_no_source(db: Database, tmp_path: pathlib.Path, clock: Callable[[], datetime]) -> None:
    """A discoverer that cannot discover anything leaves every source of its type as stored."""
    MongoCatalogRepository(db).save({"dataset_id": "kept", "sources": [_entry(SERVER_A)]})
    stored = _stored_sources(db)

    _build(db, tmp_path, FakeHapiDiscoverer([], set(), DataRetrievalError("server list unreadable")), clock)

    assert _stored_sources(db) == stored
