import json
import logging
import pathlib
import time
from typing import Any, Dict, List

from hapiclient import hapi
from hapiclient.util import HAPIError

logger = logging.getLogger(__name__)


def build_hapi_catalog(
    server_list_path: pathlib.Path, catalog_output_path: pathlib.Path, include_test_servers: bool = False
):
    """
    Builds a master catalog by querying all known HAPI servers.

    Reads a list of server URLs (like all.txt), queries each server's
    /catalog endpoint, and then queries the /info endpoint
    for *each* dataset to get its time range.

    The output format is:
    {
      "dataset_id": {
        "server": "server_url",
        "time_range": {
          "start_time": "YYYY-MM-DDTHH:MM:SSZ",
          "end_time": "YYYY-MM-DDTHH:MM:SSZ"
        }
      }, ...
    }

    Args:
        server_list_path (pathlib.Path): Path to the 'all.txt' file.
        catalog_output_path (pathlib.Path): Path to write the
                                            'hapi_master_catalog.json' file.
        include_test_servers (bool): Whether to include 'TestData' servers.
    """
    logger.info("--- Starting HAPI Catalog Build ---")
    master_catalog: Dict[str, List[Dict[str, Any]]] = {}

    # --- 1. Read Server List ---
    if not server_list_path.exists():
        logger.error(f"HAPI server list not found at: {server_list_path}", exc_info=True)
        return

    logger.info(f"Reading server list from: {server_list_path}")
    lines = server_list_path.read_text().splitlines()

    server_urls = []
    for line in lines:
        url = line.strip()
        if url and not url.startswith("#"):
            if "TestData" in url and not include_test_servers:
                logger.info(f"Ignoring TestData server: {url}")
                continue
            server_urls.append(url)

    logger.info(f"Found {len(server_urls)} HAPI servers to query.")

    # --- 2. Build Catalog ---
    for server_url in server_urls:
        try:
            logger.info(f"Querying catalog from: {server_url}")
            # HAPI call without a dataset ID fetches the /catalog endpoint
            meta_catalog = hapi(server_url, logging=True)

            catalog = meta_catalog.get("catalog")
            if not catalog:
                logger.warning(f"No catalog found for server: {server_url}")
                continue

            logger.info(
                f"Found {len(catalog)} datasets on {server_url}. Fetching info..."
            )
            for dataset in catalog[:10]:
                dataset_id = dataset.get("id")
                if not dataset_id:
                    continue

                try:
                    # Get /info endpoint for this dataset
                    logger.debug(f"Getting info for {dataset_id} from {server_url}")
                    info_meta = hapi(server_url, dataset_id, logging=True) 
                    
                    start_date = info_meta.get("startDate")
                    stop_date = info_meta.get("stopDate")

                    if start_date and stop_date:
                        new_entry = {
                            "server": server_url,
                            "time_range": {
                                "start_time": start_date,
                                "end_time": stop_date,
                            },
                        }
                        
                        if dataset_id not in master_catalog:
                            # This is the first time we see this dataset
                            master_catalog[dataset_id] = []
                        
                        master_catalog[dataset_id].append(new_entry)
                    else:
                        logger.warning(f"Dataset {dataset_id} on {server_url} has no startDate/stopDate. Skipping.")

                except HAPIError as e:
                    logger.error(f"Failed to get info for {dataset_id} from {server_url}: {e}")

        except HAPIError as e:
            logger.error(f"Failed to query server {server_url}: {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred for {server_url}: {e}", exc_info=True)

    # --- 3. Save Catalog ---
    try:
        # Ensure the output directory exists
        catalog_output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(catalog_output_path, "w", encoding="utf-8") as f:
            json.dump(master_catalog, f, indent=2)
        logger.info(f"Master catalog built successfully with {len(master_catalog)} datasets.")
        logger.info(f"Catalog saved to: {catalog_output_path}")

    except IOError as e:
        logger.error(f"Failed to write catalog file: {e}", exc_info=True)

    logger.info("--- HAPI Catalog Build Finished ---")
