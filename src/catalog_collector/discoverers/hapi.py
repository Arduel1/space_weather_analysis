import logging
import pathlib
from typing import Any, Dict, Generator, List

from catalog_collector.discoverers.base import AbstractDiscoverer
from space_weather_analysis.clients.hapi import HapiClient
from space_weather_analysis.util.types import CatalogEntryDict, DiscoveryYield

logger = logging.getLogger(__name__)


class HapiDiscoverer(AbstractDiscoverer):
    """Discovers datasets by querying a list of HAPI-compliant servers."""

    HAPI_SOURCE_ID = "HAPI"

    def __init__(
        self,
        server_list_path: pathlib.Path,
        include_test_servers: bool = False,
    ):
        """Initializes the HapiDiscoverer.

        Args:
            server_list_path (pathlib.Path): Path to the text file
                                             listing server URLs.
            include_test_servers (bool): Whether to include servers
                                         marked as test servers.
        """
        self.server_list_path = server_list_path
        self.include_test_servers = include_test_servers
        # We initialize the client without a DB connection as we only use direct fetch methods
        self.client = HapiClient(db=None)

    def _read_server_list(self) -> List[str]:
        """Reads and filters the list of server URLs from the text file.

        Returns:
            List[str]: A list of server URLs.
        """
        if not self.server_list_path.exists():
            logger.error(f"Server list file not found at: {self.server_list_path}")
            return []

        logger.info(f"Reading server list from: {self.server_list_path}")
        lines = self.server_list_path.read_text().splitlines()

        server_urls = []
        for line in lines:
            url = line.strip()
            # Skip empty lines or lines that are comments
            if not url or url.startswith("#"):
                continue

            # Skip test servers if not included
            if "TestData" in url and not self.include_test_servers:
                logger.info(f"Ignoring TestData server: {url}")
                continue

            server_urls.append(url)
        return server_urls

    def _get_entry_for_dataset(
        self,
        server_url: str,
        dataset: Dict[str, Any],
    ) -> Generator[DiscoveryYield, None, None]:
        """Retrieves the metadata for a single dataset as a catalog entry tuple.

        Args:
            server_url (str): The URL of the server to query.
            dataset (Dict[str, Any]): The dataset dictionary from the catalog.

        Yields: A generator yielding a single (dataset_id, entry, metadata) tuple.
        """
        dataset_id = dataset.get("id")
        if not dataset_id:
            return

        # Try to get basic info from the catalog entry first
        start_date = dataset.get("startDate")
        stop_date = dataset.get("stopDate")
        info_meta: Dict[str, Any] = {}

        # Fetch metadata from server (/info endpoint)
        # This provides the description and other details needed for the metadata collection.
        try:
            info_meta = self.client.fetch_dataset_metadata_from_server(dataset_id, server_url) or {}
        except Exception:
            logger.warning(f"Could not fetch metadata for {dataset_id} from {server_url}")

        # If dates were missing in catalog, try to fill them from info_meta
        if start_date and stop_date:
            logger.info(f"Using metadata from catalog list for '{dataset_id}'.")
        elif info_meta:
            logger.info(f"Metadata missing in catalog for '{dataset_id}'. Using /info result.")
            start_date = info_meta.get("startDate")
            stop_date = info_meta.get("stopDate")

        if start_date and stop_date:
            new_entry: CatalogEntryDict = {
                "type": self.HAPI_SOURCE_ID,
                "source_id": server_url,
                "time_range": {
                    "start_time": str(start_date),
                    "end_time": str(stop_date),
                },
            }
            logger.info(f"Successfully captured metadata for '{dataset_id}': {start_date} -> {stop_date}")
            yield dataset_id, new_entry, info_meta
        else:
            logger.warning(f"Dataset {dataset_id} on {server_url} has no time range. Skipping.")

    def _discover_datasets_for_server(self, server_url: str) -> Generator[DiscoveryYield, None, None]:
        """Retrieves the full dataset catalog from a single server.

        Args:
            server_url (str): The URL of the server to query.

        Yields:
            Generator of catalog entries from this server.
        """
        try:
            catalog = self.client.fetch_server_catalog(server_url)
        except Exception:
            # Error logging is handled in the client
            return

        total_datasets = len(catalog)
        logger.info(f"Found {total_datasets} datasets on {server_url} Fetching info...")

        for index, dataset in enumerate(catalog, start=1):
            dataset_id = dataset.get("id")
            if not dataset_id:
                continue

            remaining = total_datasets - index
            logger.info(f"Processing dataset {index}/{total_datasets}: '{dataset_id}' (Remaining: {remaining})")

            yield from self._get_entry_for_dataset(server_url, dataset)

            if index < total_datasets:
                next_id = catalog[index].get("id", "Unknown")
                logger.info(f"Finished '{dataset_id}'. Next up: '{next_id}'")

    def discover(self) -> Generator[DiscoveryYield, None, None]:
        """Implements the discovery process for HAPI sources.

        Iterates through the list of servers, queries each for its
        catalog, and yields standardized entries.
        """
        server_urls = self._read_server_list()
        logger.info(f"--- Starting HAPI Source Discovery ({len(server_urls)} servers) ---")

        for server_url in server_urls:
            try:
                logger.info(f"Querying catalog from: {server_url}")
                yield from self._discover_datasets_for_server(server_url)
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred for {server_url}: {e}",
                    exc_info=True,
                )
        logger.info("--- HAPI Source Discovery Finished ---")
