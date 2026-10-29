import json
import logging
import pathlib
from typing import Any, Dict, List, Optional, Tuple

from pymongo import errors as pymongo_errors
from pymongo.database import Database

from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository

logger = logging.getLogger(__name__)

# Source fields the availability refresh maintains; a snapshot reload keeps them for refreshed sources.
AVAILABILITY_FIELDS = ("time_range", "ongoing", "availability_checked_at", "missing_runs", "missing_since")


class CatalogSnapshotLoader:
    """Loads the master catalog file into MongoDB.

    The loader reads the generated JSON catalog and updates the MongoDB
    collection, so the central database reflects the latest discovery results.
    """

    def __init__(self, db: Database, file_path: pathlib.Path) -> None:
        """Initializes the loader.

        Args:
            db: The MongoDB database instance.
            file_path: Path to the catalog JSON file.
        """
        self.file_path = file_path
        self.catalog_repository: CatalogRepository = MongoCatalogRepository(db)

    def _load_json(self) -> Optional[List[MasterCatalogItem]]:
        """Reads and parses the catalog JSON file.

        Returns:
            Optional[List[MasterCatalogItem]]: List of catalog items or None.
        """
        if not self.file_path.exists():
            logger.error(f"Catalog file not found: {self.file_path}")
            return None

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            return [{"dataset_id": k, "sources": v} for k, v in data.items()]
        except Exception as e:
            logger.error(f"Failed to load JSON catalog: {e}", exc_info=True)
            return None

    def _get_sortable_key(self, source: CatalogEntryDict) -> Tuple[Any, ...]:
        """Helper for stable sorting of sources."""
        tr = source.get("time_range", {})
        return (
            source.get("type"),
            source.get("source_id"),
            (tr.get("start_time"), tr.get("end_time")),
        )

    def load_to_db(self) -> None:
        """Upserts catalog entries into the MongoDB collection."""
        logger.info(f"--- Loading Catalog Cache from {self.file_path} to MongoDB ---")
        items = self._load_json()
        if not items:
            logger.warning("Catalog file is empty or missing. Starting with empty DB.")
            return
        logger.info(f"Found {len(items)} datasets in catalog file.")

        # We sort sources to ensure deterministic comparison/storage if needed
        for item in items:
            item["sources"].sort(key=self._get_sortable_key)

        self._keep_refreshed_availability(items)

        try:
            self.catalog_repository.save_many(items)
        except pymongo_errors.BulkWriteError as bwe:
            logger.error(f"Bulk write error: {bwe.details}")
        except Exception as e:
            logger.error(f"DB Load failed: {e}", exc_info=True)

        logger.info("--- Catalog Load Finished ---")

    def _keep_refreshed_availability(self, items: List[MasterCatalogItem]) -> None:
        """Carries the availability state of already refreshed sources over into the snapshot items.

        The snapshot holds the time ranges of its creation; a source refreshed from the HAPI mirror keeps
        its newer time range, ongoing state and miss count, so a collector restart with an unreachable
        mirror does not fall back to stale ranges.

        Args:
            items: The snapshot items, changed in place.
        """
        refreshed_sources: Dict[Tuple[str, str, str], CatalogEntryDict] = {}
        for stored_item in self.catalog_repository.get_all():
            for stored_source in stored_item["sources"]:
                if "availability_checked_at" in stored_source:
                    source_key = (stored_item["dataset_id"], stored_source.get("type", ""), stored_source.get("source_id", ""))
                    refreshed_sources[source_key] = stored_source

        for item in items:
            for source in item["sources"]:
                refreshed_source = refreshed_sources.get((item["dataset_id"], source.get("type", ""), source.get("source_id", "")))
                if refreshed_source is None:
                    continue
                for field in AVAILABILITY_FIELDS:
                    if field in refreshed_source:
                        source[field] = refreshed_source[field]  # type: ignore[literal-required]

    def upsert_dataset(self, dataset_id: str, sources: List[CatalogEntryDict]) -> None:
        """Upserts a single dataset entry into MongoDB."""
        # Sort sources to ensure deterministic storage
        sorted_sources = sorted(sources, key=self._get_sortable_key)

        try:
            self.catalog_repository.save({"dataset_id": dataset_id, "sources": sorted_sources})
            logger.info(f"DB Saved: {dataset_id} (Sources: {len(sorted_sources)})")
        except Exception as e:
            logger.error(f"Failed to upsert dataset {dataset_id}: {e}")

    def delete_dataset(self, dataset_id: str) -> None:
        """Deletes a dataset from the MongoDB collection."""
        try:
            if self.catalog_repository.delete(dataset_id):
                logger.info(f"DB Deleted: {dataset_id}")
            else:
                logger.info(f"DB Delete skipped (not found): {dataset_id}")
        except Exception as e:
            logger.error(f"Failed to delete dataset {dataset_id}: {e}")
