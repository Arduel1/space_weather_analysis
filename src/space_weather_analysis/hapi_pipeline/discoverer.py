import logging
import pathlib
from typing import Any, Dict, Generator, List, Tuple

from hapiclient import hapi
from hapiclient.util import HAPIError

from space_weather_analysis.config import HAPI_SOURCE_ID
from space_weather_analysis.util.catalog.abstract_discoverer import AbstractDiscoverer

logger = logging.getLogger(__name__)


class HapiDiscoverer(AbstractDiscoverer):
    """
    Discovers datasets by querying a list of HAPI-compliant servers.
    """

    def __init__(
        self,
        server_list_path: pathlib.Path,
        include_test_servers: bool = False,
    ):
        """
        Initializes the HapiDiscoverer.

        Args:
            server_list_path (pathlib.Path): Path to the text file
                                             listing server URLs.
            include_test_servers (bool): Whether to include servers
                                         marked as test servers.
        """
        self.server_list_path = server_list_path
        self.include_test_servers = include_test_servers

    def _read_server_list(self) -> List[str]:
        """
        Reads and filters the list of server URLs from the text file.

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
        self, server_url: str, dataset_id: str
    ) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
        """
        Retrieves the metadata for a single dataset and yields it
        as a catalog entry tuple.

        Handles errors for this specific dataset lookup.

        Args:
            server_url (str): The URL of the server to query.
            dataset_id (str): The specific dataset ID to get info for.

        Yields:
            Generator[Tuple[str, Dict[str, Any]], None, None]:
            A generator yielding a single (dataset_id, entry) tuple
            if successful.
        """
        try:
            logger.info(f"Getting info for {dataset_id} from {server_url}")
            info_meta = hapi(server_url, dataset_id, logging=True)

            start_date = info_meta.get("startDate")
            stop_date = info_meta.get("stopDate")

            if start_date and stop_date:
                new_entry = {
                    "type": HAPI_SOURCE_ID,
                    "source_id": server_url,
                    "time_range": {
                        "start_time": start_date,
                        "end_time": stop_date,
                    },
                }
                yield dataset_id, new_entry
            else:
                logger.warning(f"Dataset {dataset_id} on {server_url} has no time range. Skipping.")
        except HAPIError as e:
            logger.error(
                f"Failed to get info for {dataset_id} from {server_url}: {e}",
                exc_info=True,
            )

    def _discover_datasets_for_server(self, server_url: str) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
        """
        Retrieves the full dataset catalog from a single server and
        yields entries for each valid dataset.

        Args:
            server_url (str): The URL of the server to query.

        Yields:
            Generator[Tuple[str, Dict[str, Any]], None, None]:
            A generator of catalog entries from this server.
        """
        meta_catalog = hapi(server_url, logging=True)
        catalog = meta_catalog.get("catalog")

        if not catalog:
            logger.warning(f"No catalog found for server: {server_url}")
            return  # Stop the generator

        logger.info(f"Found {len(catalog)} datasets on {server_url}. Fetching info...")
        for dataset in catalog:
            dataset_id = dataset.get("id")
            if not dataset_id:
                continue

            # Yield from the helper method that processes a single dataset
            yield from self._get_entry_for_dataset(server_url, dataset_id)

    def discover(self) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
        """
        Implements the discovery process for HAPI sources.

        Iterates through the list of servers, queries each for its
        catalog, and yields standardized entries for each dataset found.
        """
        server_urls = self._read_server_list()
        logger.info(f"--- Starting {HAPI_SOURCE_ID} Source Discovery ({len(server_urls)} servers) ---")
        for server_url in server_urls:
            try:
                logger.info(f"Querying catalog from: {server_url}")
                # Yield from the helper that processes a whole server
                yield from self._discover_datasets_for_server(server_url)

            except HAPIError as e:
                logger.error(f"Failed to query server {server_url}: {e}", exc_info=True)
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred for {server_url}: {e}",
                    exc_info=True,
                )
        logger.info(f"--- {HAPI_SOURCE_ID} Source Discovery Finished ---")
