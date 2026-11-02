import socket
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import requests
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem
from space_weather_analysis.catalog.repository import MongoCatalogRepository

# The path under which the fake CDAS serves its dataset list, the path of the real service.
CDAS_PATH = "/WS/cdasr/1/dataviews/sp_phys/datasets"

# The fixed time of the fake clock.
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    """The fixed current time of the fake clock."""
    return NOW


@pytest.fixture
def clock(now: datetime) -> Callable[[], datetime]:
    """A clock that always returns the fixed current time."""
    return lambda: now


@pytest.fixture
def http_session() -> Iterator[requests.Session]:
    """An HTTP session for the refreshers, closed after the test."""
    with requests.Session() as session:
        yield session


@pytest.fixture
def closed_port_url() -> str:
    """A local base URL on a port nobody listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture
def save_catalog(db: Database) -> Callable[[dict[str, list[CatalogEntryDict]]], list[MasterCatalogItem]]:
    """Saves catalog items given as sources per dataset ID and returns the catalog as stored."""

    def save(items: dict[str, list[CatalogEntryDict]]) -> list[MasterCatalogItem]:
        """Saves the items and reads the whole catalog back."""
        repository = MongoCatalogRepository(db)
        repository.save_many([{"dataset_id": dataset_id, "sources": sources} for dataset_id, sources in items.items()])
        return repository.get_all()

    return save


@pytest.fixture
def serve_cdas(httpserver: HTTPServer) -> Callable[[list[dict[str, Any]]], str]:
    """Lets the fake CDAS serve a dataset list with the given entries and returns the list URL."""

    def serve(entries: list[dict[str, Any]]) -> str:
        """Replaces the served list with the entries."""
        httpserver.clear()
        httpserver.expect_request(CDAS_PATH).respond_with_json({"DatasetDescription": entries})
        return httpserver.url_for(CDAS_PATH)

    return serve
