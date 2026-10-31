import logging
import pathlib
from collections.abc import Iterator
from typing import Any

from hapiclient.util import HAPIError

from catalog_collector.data_catalog.discovery.discoverers.base import AbstractDiscoverer, DiscoveryYield
from space_weather_analysis.catalog.models import CatalogEntryDict, SourceType
from space_weather_analysis.clients.hapi import HapiClient
from space_weather_analysis.util.error_handling import DataRetrievalError

logger = logging.getLogger(__name__)

# Servers of the HAPI project that serve synthetic test datasets carry this text in their URL.
TEST_SERVER_MARKER = "TestData"


class HapiDiscoverer(AbstractDiscoverer):
    """Lists the datasets of every HAPI server of a server list as catalog entries with their /info responses."""

    source_type = SourceType.HAPI

    def __init__(self, server_list_path: pathlib.Path, client: HapiClient | None = None, *, include_test_servers: bool = False) -> None:
        """Initializes the discoverer.

        Args:
            server_list_path: The text file listing one server URL per line; empty lines and lines starting with '#' are ignored.
            client: The HAPI client; a new one if None.
            include_test_servers: Whether servers of synthetic test datasets are queried too.
        """
        super().__init__()
        self.server_list_path = server_list_path
        self.client = client or HapiClient()
        self.include_test_servers = include_test_servers

    def discover(self) -> Iterator[DiscoveryYield]:
        """Yields the datasets of every listed server; a server whose catalog cannot be read is recorded as failed source.

        Yields:
            The dataset ID, the catalog entry and the /info response, empty if it could not be read.

        Raises:
            DataRetrievalError: If the server list cannot be read.
        """
        for server_url in self._read_server_list():
            try:
                catalog = self.client.fetch_server_catalog(server_url)
            except (HAPIError, DataRetrievalError) as e:
                logger.warning("HAPI server %s could not be listed: %s", server_url, e)
                self.failed_source_ids.add(server_url)
                continue
            logger.info("HAPI discovery: %d datasets at %s.", len(catalog), server_url)
            for dataset in catalog:
                discovered = self._entry_for_dataset(server_url, dataset)
                if discovered is not None:
                    yield discovered

    def _read_server_list(self) -> list[str]:
        """Reads the server URLs of the server list.

        Returns:
            The server URLs in file order, without test servers unless they are included.

        Raises:
            DataRetrievalError: If the server list cannot be read.
        """
        try:
            lines = self.server_list_path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            raise DataRetrievalError(f"HAPI server list {self.server_list_path} cannot be read: {e}") from e
        server_urls = []
        for line in lines:
            url = line.strip()
            if not url or url.startswith("#") or (TEST_SERVER_MARKER in url and not self.include_test_servers):
                continue
            server_urls.append(url)
        return server_urls

    def _entry_for_dataset(self, server_url: str, dataset: dict[str, Any]) -> DiscoveryYield | None:
        """Builds the catalog entry of one dataset, taking its time range from the catalog response or else from its /info response.

        Args:
            server_url: The base URL of the HAPI server.
            dataset: The dataset entry of the server's /catalog response.

        Returns:
            The dataset ID, entry and /info response, or None if the dataset has no ID or no time range.
        """
        dataset_id = dataset.get("id")
        if not dataset_id:
            return None
        try:
            info = self.client.fetch_dataset_metadata_from_server(dataset_id, server_url) or {}
        except DataRetrievalError as e:
            logger.warning("Metadata of %s at %s could not be read: %s", dataset_id, server_url, e)
            info = {}
        start_date = dataset.get("startDate") or info.get("startDate")
        stop_date = dataset.get("stopDate") or info.get("stopDate")
        if not start_date or not stop_date:
            logger.debug("Dataset %s at %s has no time range; skipped.", dataset_id, server_url)
            return None
        entry: CatalogEntryDict = {
            "type": SourceType.HAPI,
            "source_id": server_url,
            "time_range": {"start_time": str(start_date), "end_time": str(stop_date)},
        }
        return dataset_id, entry, info
