import json
import logging
import pathlib
from typing import Any, Dict, List

from space_weather_analysis.util.catalog.abstract_discoverer import AbstractDiscoverer

logger = logging.getLogger(__name__)


class UniversalCatalogManager:
    """
    Builds a master catalog by orchestrating multiple discovery strategies.

    This class iterates over a list of 'AbstractDiscoverer' objects,
    collects their results, and merges them into a single, sorted
    master catalog file.
    """

    def __init__(
        self,
        discoverers: List[AbstractDiscoverer],
        catalog_output_path: pathlib.Path,
    ):
        """
        Initializes the UniversalCatalogManager.

        Args:
            discoverers (List[AbstractDiscoverer]): A list of concrete
                discoverer objects (e.g., HapiDiscoverer, FidoCdawebDiscoverer).
            catalog_output_path (pathlib.Path): Path where the generated
                master catalog JSON will be saved.
        """
        self.discoverers = discoverers
        self.catalog_output_path = catalog_output_path
        self.master_catalog: Dict[str, List[Dict[str, Any]]] = {}
        self.lowercase_id_map: Dict[str, str] = {}

    def _get_canonical_dataset_key(self, dataset_id: str) -> str:
        """
        Finds the existing canonical key for a dataset, ignoring case.

        Args:
            dataset_id (str): The dataset ID to check.

        Returns:
            str: The canonical (case-preserved) dataset ID.
        """
        lowercase_id = dataset_id.lower()
        if lowercase_id not in self.lowercase_id_map:
            self.lowercase_id_map[lowercase_id] = dataset_id
            return dataset_id
        else:
            return self.lowercase_id_map[lowercase_id]

    def _add_entry(self, dataset_id: str, entry: Dict[str, Any]) -> None:
        """
        Helper to add a new source entry to the master catalog.

        Prevents duplicate entries based on type and source_id.

        Args:
            dataset_id (str): The canonical dataset ID.
            entry (Dict[str, Any]): The new source entry to add.
        """
        if dataset_id not in self.master_catalog:
            self.master_catalog[dataset_id] = []

        # Check for duplicates
        for existing_entry in self.master_catalog[dataset_id]:
            if existing_entry["type"] == entry["type"] and existing_entry["source_id"] == entry["source_id"]:
                logger.debug(f"Skipping duplicate entry for {dataset_id} from {entry['source_id']}")
                return

        self.master_catalog[dataset_id].append(entry)

    def build_catalog(self) -> None:
        """
        Runs all discovery tasks and saves the universal catalog.
        """
        logger.info("--- Starting Universal Catalog Build ---")
        self.master_catalog.clear()
        self.lowercase_id_map.clear()

        # Iterate over all registered discoverer strategies
        for discoverer in self.discoverers:
            logger.info(f"Running discoverer: {discoverer.__class__.__name__}")
            try:
                # Consume the generator from the discoverer
                for dataset_id, entry in discoverer.discover():
                    # Behebt Pylance-Fehler: Stelle sicher, dass die ID ein str ist
                    key_to_use = self._get_canonical_dataset_key(str(dataset_id))
                    self._add_entry(key_to_use, entry)
            except Exception as e:
                logger.error(
                    f"Discoverer {discoverer.__class__.__name__} failed: {e}",
                    exc_info=True,
                )

        # Save the final catalog
        try:
            logger.info(f"Sorting {len(self.master_catalog)} unique dataset IDs alphabetically...")
            sorted_items = sorted(
                self.master_catalog.items(),
                key=lambda item: item[0].lower(),
            )
            sorted_master_catalog = dict(sorted_items)

            self.catalog_output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.catalog_output_path, "w", encoding="utf-8") as f:
                json.dump(sorted_master_catalog, f, indent=2)

            logger.info(f"Universal catalog built successfully with {len(self.master_catalog)} unique dataset IDs.")
            logger.info(f"Catalog saved to: {self.catalog_output_path}")

        except IOError as e:
            logger.error(f"Failed to write catalog file: {e}", exc_info=True)

        logger.info("--- Universal Catalog Build Finished ---")
