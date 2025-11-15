import logging
import pathlib
from typing import Any, Dict, Generator, List, Tuple

from hapiclient import hapi
from hapiclient.util import HAPIError

from space_weather_analysis.catalog.abstract_discoverer import AbstractDiscoverer

logger = logging.getLogger(__name__)


class HapiDiscoverer(AbstractDiscoverer):
    """
    Discovers datasets by querying a list of HAPI servers.
    """

    def __init__(self, server_list_path: pathlib.Path, include_test_servers: bool = False):
        """
        Initializes the HapiDiscoverer.

        Args:
            server_list_path (pathlib.Path): Path to the text file
                                             listing HAPI server URLs.
            include_test_servers (bool): Whether to include servers
                                         with 'TestData' in their URL.
        """
        self.server_list_path = server_list_path
        self.include_test_servers = include_test_servers

    def _read_server_list(self) -> List[str]:
        """
        Reads the list of HAPI server URLs from the text file.

        Returns:
            List[str]: A list of HAPI server URLs.
        """
        if not self.server_list_path.exists():
            logger.error(f"HAPI server list not found at: {self.server_list_path}")
            return []

        logger.info(f"Reading server list from: {self.server_list_path}")
        lines = self.server_list_path.read_text().splitlines()

        server_urls = []
        for line in lines:
            url = line.strip()
            if url and not url.startswith("#"):
                if "TestData" in url and not self.include_test_servers:
                    logger.info(f"Ignoring TestData server: {url}")
                    continue
                server_urls.append(url)
        return server_urls

    def discover(self) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
        """
        Iterate all HAPI servers and yield catalog entries.
        """
        server_urls = self._read_server_list()
        logger.info(f"--- Starting HAPI Source Discovery ({len(server_urls)} servers) ---")
        for server_url in server_urls:
            try:
                logger.info(f"Querying catalog from: {server_url}")
                meta_catalog = hapi(server_url, logging=True)

                catalog = meta_catalog.get("catalog")
                if not catalog:
                    logger.warning(f"No catalog found for server: {server_url}")
                    continue

                logger.info(f"Found {len(catalog)} datasets on {server_url}. Fetching info...")
                for dataset in catalog:
                    dataset_id = dataset.get("id")
                    if not dataset_id:
                        continue

                    try:
                        logger.debug(f"Getting info for {dataset_id} from {server_url}")
                        info_meta = hapi(server_url, dataset_id, logging=True)

                        start_date = info_meta.get("startDate")
                        stop_date = info_meta.get("stopDate")

                        if start_date and stop_date:
                            new_entry = {
                                "type": "HAPI",
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

            except HAPIError as e:
                logger.error(f"Failed to query server {server_url}: {e}", exc_info=True)
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred for {server_url}: {e}",
                    exc_info=True,
                )
        logger.info("--- HAPI Source Discovery Finished ---")
