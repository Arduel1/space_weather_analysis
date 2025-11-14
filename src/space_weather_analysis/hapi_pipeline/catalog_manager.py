import json
import logging
import pathlib
from typing import Any, Dict, List

from hapiclient import hapi
from hapiclient.util import HAPIError

from space_weather_analysis.sunpy_pipeline.cdaweb_client import CdawebClient

logger = logging.getLogger(__name__)


class UniversalCatalogManager:
    """
    Builds a master catalog by querying *all* known data sources.

    1. Queries all HAPI servers from a list.
    2. Queries the Sunpy/CDAWeb source.

    The output format supports different source types and fallbacks:
    {
      "dataset_id": [
        {
          "type": "HAPI" | "FIDO_CDAWEB",
          "source_id": "server_url" | "observatory_group",
          "time_range": {
            "start_time": "...",
            "end_time": "..."
          }
        },
        ...
      ]
    }
    """

    def __init__(self, server_list_path: pathlib.Path, catalog_output_path: pathlib.Path):
        self.server_list_path = server_list_path
        self.catalog_output_path = catalog_output_path
        self.master_catalog: Dict[str, List[Dict[str, Any]]] = {}
        self.lowercase_id_map: Dict[str, str] = {}

    def _get_canonical_dataset_key(self, dataset_id: str) -> str:
        """
        Finds the existing canonical key for a dataset, ignoring case.
        If not found, registers the provided dataset_id as the canonical key.
        """
        lowercase_id = dataset_id.lower()
        if lowercase_id not in self.lowercase_id_map:
            # First time seeing this ID. Use the provided case as canonical.
            self.lowercase_id_map[lowercase_id] = dataset_id
            return dataset_id
        else:
            # We've seen a version of this ID before. Use the first one we saw.
            return self.lowercase_id_map[lowercase_id]

    def _add_entry(self, dataset_id: str, entry: Dict[str, Any]):
        """Helper to add a new source entry to the master catalog."""
        if dataset_id not in self.master_catalog:
            self.master_catalog[dataset_id] = []

        # Check for duplicates (e.g., if Fido and HAPI report the same CDAWeb dataset)
        # We identify duplicates by type and source_id
        for existing_entry in self.master_catalog[dataset_id]:
            if existing_entry["type"] == entry["type"] and existing_entry["source_id"] == entry["source_id"]:
                logger.debug(f"Skipping duplicate entry for {dataset_id} from {entry['source_id']}")
                return

        self.master_catalog[dataset_id].append(entry)

    def _build_from_hapi_sources(self, server_urls: List[str]):
        """
        Task 1: Iterate all HAPI servers and add them to the catalog.
        """
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
                for dataset in catalog:  # TODO: Limit to first 20 (only for testing)
                    dataset_id = dataset.get("id")
                    if not dataset_id:
                        continue

                    try:
                        logger.debug(f"Getting info for {dataset_id} from {server_url}")
                        info_meta = hapi(server_url, dataset_id, logging=True)

                        start_date = info_meta.get("startDate")
                        stop_date = info_meta.get("stopDate")

                        if start_date and stop_date:
                            key_to_use = self._get_canonical_dataset_key(dataset_id)
                            new_entry = {
                                "type": "HAPI",
                                "source_id": server_url,
                                "time_range": {
                                    "start_time": start_date,
                                    "end_time": stop_date,
                                },
                            }
                            self._add_entry(key_to_use, new_entry)
                        else:
                            logger.warning(f"Dataset {dataset_id} on {server_url} has no time range. Skipping.")

                    except HAPIError as e:
                        logger.error(f"Failed to get info for {dataset_id} from {server_url}: {e}", exc_info=True)

            except HAPIError as e:
                logger.error(f"Failed to query server {server_url}: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"An unexpected error occurred for {server_url}: {e}", exc_info=True)
        logger.info("--- HAPI Source Discovery Finished ---")

    def _build_from_fido_cdaweb_source(self):
        """
        Task 2: Iterate all Fido/CDAWeb datasets and add them.
        """
        logger.info("--- Starting FIDO_CDAWEB Source Discovery ---")
        try:
            client = CdawebClient()

            # 1. Get all groups
            all_groups = client.get_observatory_groups()
            if not all_groups:
                logger.error("FIDO_CDAWEB: Could not get observatory groups.")
                return

            logger.info(f"FIDO_CDAWEB: Found {len(all_groups)} groups.")

            # 2. Iterate groups
            for group_row in all_groups:  # TODO: Limit to first 20 (only for testing)
                group_name = group_row["Group"]
                logger.info(f"FIDO_CDAWEB: Getting datasets for group: {group_name}")

                datasets_table = client.get_datasets_for_group(group_name)
                if datasets_table is None or len(datasets_table) == 0:
                    logger.warning(f"FIDO_CDAWEB: No datasets found for group {group_name}.")
                    continue

                # 3. Iterate datasets
                for dataset_row in datasets_table:
                    try:
                        dataset_id = dataset_row["Id"]
                        start_date = dataset_row["Start"]
                        stop_date = dataset_row["End"]

                        if start_date and stop_date:
                            key_to_use = self._get_canonical_dataset_key(dataset_id)
                            new_entry = {
                                "type": "FIDO_CDAWEB",
                                "source_id": group_name,  # We need the group for the download path
                                "time_range": {
                                    # Ensure time strings are standard
                                    "start_time": start_date,
                                    "end_time": stop_date,
                                },
                            }
                            self._add_entry(key_to_use, new_entry)
                        else:
                            logger.warning(f"Dataset {dataset_id} (Group {group_name}) has no time range. Skipping.")

                    except KeyError as e:
                        # Catch if 'Id', 'Start', or 'End' columns are missing
                        logger.error(
                            f"FIDO_CDAWEB: Row for group {group_name} is malformed. "
                            f"Missing key: {e}. Row: {dataset_row}",
                            exc_info=True,
                        )
                    except Exception as e:
                        # Catch other unexpected row-level errors
                        logger.error(f"FIDO_CDAWEB: Failed to process dataset row {dataset_row}: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"FIDO_CDAWEB source discovery failed: {e}", exc_info=True)
        logger.info("--- FIDO_CDAWEB Source Discovery Finished ---")

    def _read_server_list(self, include_test_servers: bool) -> List[str]:
        """Reads the list of HAPI server URLs from the text file."""
        if not self.server_list_path.exists():
            logger.error(f"HAPI server list not found at: {self.server_list_path}")
            return []

        logger.info(f"Reading server list from: {self.server_list_path}")
        lines = self.server_list_path.read_text().splitlines()

        server_urls = []
        for line in lines:
            url = line.strip()
            if url and not url.startswith("#"):
                if "TestData" in url and not include_test_servers:
                    logger.info(f"Ignoring TestData server: {url}")
                    continue
                server_urls.append(url)
        return server_urls

    def build_catalog(self, include_test_servers: bool = False):
        """
        Runs all discovery tasks and saves the universal catalog.
        """
        logger.info("--- Starting Universal Catalog Build ---")

        # 1. Read HAPI servers list
        server_urls = self._read_server_list(include_test_servers)

        # 2. Run HAPI discovery
        self._build_from_hapi_sources(server_urls)

        # 3. Run Fido/CDAWeb discovery
        self._build_from_fido_cdaweb_source()

        # 4. Save Catalog
        try:
            logger.info(f"Sorting {len(self.master_catalog)} unique dataset IDs alphabetically...")
            sorted_items = sorted(
                self.master_catalog.items(),
                key=lambda item: item[0].lower(),  # Sort by lowercase key
            )
            # Create a new dictionary that preserves this order
            sorted_master_catalog = dict(sorted_items)

            self.catalog_output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.catalog_output_path, "w", encoding="utf-8") as f:
                # Save the sorted catalog
                json.dump(sorted_master_catalog, f, indent=2)

            logger.info(f"Universal catalog built successfully with {len(self.master_catalog)} unique dataset IDs.")
            logger.info(f"Catalog saved to: {self.catalog_output_path}")

        except IOError as e:
            logger.error(f"Failed to write catalog file: {e}", exc_info=True)

        logger.info("--- Universal Catalog Build Finished ---")


def run_catalog_build_task(
    server_list_path: pathlib.Path,
    catalog_output_path: pathlib.Path,
    include_test_servers: bool = False,
):
    """
    Standalone function to be called from main.py.
    """
    manager = UniversalCatalogManager(server_list_path, catalog_output_path)
    manager.build_catalog(include_test_servers=include_test_servers)
