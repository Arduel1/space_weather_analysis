import json
import logging
import pathlib
from typing import Dict, List

from src.catalog_collector.discoverers.base import AbstractDiscoverer
from src.common.util.types import CatalogEntryDict

logger = logging.getLogger(__name__)


class CatalogBuilder:
    """
    Builds a master catalog by orchestrating multiple discovery strategies.

    This class iterates over a list of discoverer objects, collects their
    results, and merges them into a single, sorted master catalog file.
    """

    def __init__(
        self,
        discoverers: List[AbstractDiscoverer],
        catalog_output_path: pathlib.Path,
    ):
        """
        Initializes the CatalogBuilder.

        Args:
            discoverers (List[AbstractDiscoverer]): A list of concrete
                discoverer strategy objects.
            catalog_output_path (pathlib.Path): Path where the generated
                master catalog JSON will be saved.
        """
        self.discoverers = discoverers
        self.catalog_output_path = catalog_output_path

    def _get_canonical_dataset_key(self, dataset_id: str, lowercase_map: Dict[str, str]) -> str:
        """
        Finds the existing canonical key for a dataset, ignoring case.

        Args:
            dataset_id (str): The dataset ID to check.
            lowercase_map (Dict[str, str]): The map tracking lowercase to
                                           canonical keys.

        Returns:
            str: The canonical (case-preserved) dataset ID.
        """
        lowercase_id = dataset_id.lower()
        if lowercase_id not in lowercase_map:
            lowercase_map[lowercase_id] = dataset_id
            return dataset_id
        else:
            return lowercase_map[lowercase_id]

    def _add_entry(
        self,
        dataset_id: str,
        entry: CatalogEntryDict,
        master_catalog: Dict[str, List[CatalogEntryDict]],
    ) -> None:
        """
        Helper to add a new source entry to the master catalog.

        Prevents duplicate entries based on 'type' and 'source_id'.

        Args:
            dataset_id (str): The canonical dataset ID.
            entry (CatalogEntryDict): The new source entry to add.
            master_catalog (Dict[str, List[CatalogEntryDict]]): The catalog
                being built.
        """
        if dataset_id not in master_catalog:
            master_catalog[dataset_id] = []

        # Check for duplicates
        for existing_entry in master_catalog[dataset_id]:
            if existing_entry["type"] == entry["type"] and existing_entry["source_id"] == entry["source_id"]:
                logger.info(f"Skipping duplicate entry for {dataset_id} from {entry['source_id']}")
                return

        master_catalog[dataset_id].append(entry)

    def _save_catalog_to_file(self, catalog_data: Dict[str, List[CatalogEntryDict]]) -> None:
        """
        Saves the completed catalog dictionary to a JSON file.

        Args:
            catalog_data (Dict[str, List[CatalogEntryDict]]): The final
                master catalog to save.
        """
        try:
            logger.info(f"Sorting {len(catalog_data)} unique dataset IDs alphabetically...")
            sorted_items = sorted(
                catalog_data.items(),
                key=lambda item: item[0].lower(),
            )
            sorted_master_catalog = dict(sorted_items)

            self.catalog_output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.catalog_output_path, "w", encoding="utf-8") as f:
                json.dump(sorted_master_catalog, f, indent=2)

            logger.info(f"Catalog saved to: {self.catalog_output_path}")

        except IOError as e:
            logger.error(f"Failed to write catalog file: {e}", exc_info=True)

    def build_catalog(self) -> None:
        """
        Runs all discovery tasks and saves the universal catalog.
        """
        logger.info("--- Starting Universal Catalog Build ---")
        master_catalog: Dict[str, List[CatalogEntryDict]] = {}
        lowercase_id_map: Dict[str, str] = {}

        for discoverer in self.discoverers:
            logger.info(f"Running discoverer: {discoverer.__class__.__name__}")
            try:
                for dataset_id, entry in discoverer.discover():
                    key_to_use = self._get_canonical_dataset_key(str(dataset_id), lowercase_id_map)
                    self._add_entry(key_to_use, entry, master_catalog)
            except Exception as e:
                logger.error(
                    f"Discoverer {discoverer.__class__.__name__} failed: {e}",
                    exc_info=True,
                )

        self._save_catalog_to_file(master_catalog)
        logger.info("--- Universal Catalog Build Finished ---")
