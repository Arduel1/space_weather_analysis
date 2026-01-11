import json
import logging
import pathlib
from typing import Dict, List, Set, Tuple

from src.catalog_collector.discoverers.base import AbstractDiscoverer
from src.catalog_collector.loader import CatalogLoader
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
        loader: CatalogLoader,
    ):
        """
        Initializes the CatalogBuilder.

        Args:
            discoverers (List[AbstractDiscoverer]): A list of concrete
                discoverer strategy objects.
            catalog_output_path (pathlib.Path): Path where the generated
                master catalog JSON will be saved.
            loader (CatalogLoader): The loader instance to update the DB.
        """
        self.discoverers = discoverers
        self.catalog_output_path = catalog_output_path
        self.loader = loader
        self.master_catalog: Dict[str, List[CatalogEntryDict]] = {}
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

    def _update_catalog(
        self,
        dataset_id: str,
        entry: CatalogEntryDict,
    ) -> bool:
        """
        Updates the master catalog with the new entry.

        Returns:
            bool: True if the catalog was updated, False otherwise.
        """
        if dataset_id not in self.master_catalog:
            self.master_catalog[dataset_id] = [entry]
            return True

        sources = self.master_catalog[dataset_id]

        # Check for duplicates or updates
        for i, existing_entry in enumerate(sources):
            if existing_entry["type"] == entry["type"] and existing_entry["source_id"] == entry["source_id"]:
                # Check if content changed (e.g. time_range)
                if existing_entry != entry:
                    logger.info(f"Updating existing entry for {dataset_id} from {entry['source_id']}")
                    sources[i] = entry
                    return True

                # Exact duplicate
                return False

        sources.append(entry)
        return True

    def _save_catalog_to_file(self) -> None:
        """
        Saves the completed catalog dictionary to a JSON file.
        """
        try:
            # logger.debug(f"Sorting {len(self.master_catalog)} unique dataset IDs alphabetically...")
            sorted_items = sorted(
                self.master_catalog.items(),
                key=lambda item: item[0].lower(),
            )
            sorted_master_catalog = dict(sorted_items)

            self.catalog_output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.catalog_output_path, "w", encoding="utf-8") as f:
                json.dump(sorted_master_catalog, f, indent=2)

            logger.info(f"Catalog saved to: {self.catalog_output_path}")

        except IOError as e:
            logger.error(f"Failed to write catalog file: {e}", exc_info=True)

    def _load_initial_catalog(self) -> None:
        """Loads the existing catalog from file if it exists."""
        if not self.catalog_output_path.exists():
            return

        try:
            with open(self.catalog_output_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self.master_catalog = data
                    for key in data.keys():
                        self.lowercase_id_map[key.lower()] = key
            logger.info(f"Loaded {len(self.master_catalog)} entries from existing catalog.")
        except Exception as e:
            logger.warning(f"Failed to load existing catalog: {e}")

    def build_catalog(self) -> None:
        """
        Runs all discovery tasks and saves the universal catalog.
        """
        logger.info("--- Starting Universal Catalog Build ---")

        self._load_initial_catalog()

        # Load existing catalog to DB first
        self.loader.load_to_db()

        # Track found entries to identify stale ones later
        # Signature: (dataset_id, source_id, type)
        found_signatures: Set[Tuple[str, str, str]] = set()

        for discoverer in self.discoverers:
            logger.info(f"Running discoverer: {discoverer.__class__.__name__}")
            try:
                for dataset_id, entry, metadata in discoverer.discover():
                    # Ensure metadata is valid before processing
                    if not entry.get("source_id") or not entry.get("type"):
                        logger.warning(f"Skipping dataset {dataset_id} due to missing metadata in entry.")
                        continue

                    key_to_use = self._get_canonical_dataset_key(str(dataset_id))

                    # Mark as found
                    found_signatures.add((key_to_use, entry["source_id"], entry["type"]))

                    if self._update_catalog(key_to_use, entry):
                        self._save_catalog_to_file()
                        self.loader.upsert_dataset(key_to_use, self.master_catalog[key_to_use])

                    # Upsert metadata if available (checked separately from catalog entry)
                    if metadata:
                        self.loader.upsert_metadata(key_to_use, metadata)

            except Exception as e:
                logger.error(
                    f"Discoverer {discoverer.__class__.__name__} failed: {e}",
                    exc_info=True,
                )

        # Delete existing entries that were not rediscovered
        self._prune_catalog(found_signatures)

        logger.info("--- Universal Catalog Build Finished ---")

    def _prune_catalog(self, found_signatures: Set[Tuple[str, str, str]]) -> None:
        """
        Removes entries from the catalog that were not found in the current run.
        """
        logger.info("--- Pruning Stale Entries ---")

        dataset_ids = list(self.master_catalog.keys())
        modified_datasets = []
        deleted_datasets = []

        for dataset_id in dataset_ids:
            sources = self.master_catalog[dataset_id]
            original_count = len(sources)

            # Keep only sources that were found in this run
            kept_sources = [
                s for s in sources
                if s.get("source_id") and s.get("type") and
                (dataset_id, s["source_id"], s["type"]) in found_signatures
            ]

            if not kept_sources:
                # No sources left, delete dataset entirely
                del self.master_catalog[dataset_id]
                deleted_datasets.append(dataset_id)
                logger.info(f"Pruning: Removing dataset {dataset_id}")
            elif len(kept_sources) != original_count:
                # Some sources removed, update dataset
                self.master_catalog[dataset_id] = kept_sources
                modified_datasets.append(dataset_id)
                logger.info(f"Pruning: Updated dataset {dataset_id} (removed {original_count - len(kept_sources)} sources)")

        # Sync changes to File and DB
        if modified_datasets or deleted_datasets:
            self._save_catalog_to_file()

            for d_id in modified_datasets:
                self.loader.upsert_dataset(d_id, self.master_catalog[d_id])

            for d_id in deleted_datasets:
                self.loader.delete_dataset(d_id)
                self.loader.delete_metadata(d_id)
