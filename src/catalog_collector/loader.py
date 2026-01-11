import json
import logging
import pathlib
from typing import Any, List, Optional, Tuple

from pymongo import ReplaceOne
from pymongo import errors as pymongo_errors
from pymongo.database import Database

from src.common.util.types import CatalogEntryDict, MasterCatalogItem

logger = logging.getLogger(__name__)


class CatalogLoader:
    """
    Loads the master catalog file into MongoDB.

    This class reads the generated JSON catalog and updates the MongoDB
    collection, ensuring that the central database reflects the latest
    discovery results.
    """

    COLLECTION_NAME = "master_catalog"
    DEFAULT_BATCH_SIZE = 1000

    def __init__(self, db: Database, file_path: pathlib.Path):
        """
        Initializes the Catalog Loader.

        Args:
            db (Database): The MongoDB database instance.
            file_path (pathlib.Path): Path to the catalog JSON file.
        """
        self.db = db
        self.file_path = file_path

    def _load_json(self) -> Optional[List[MasterCatalogItem]]:
        """
        Reads and parses the catalog JSON file.

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
        """
        Upserts catalog entries into the MongoDB collection.
        """
        logger.info("--- Loading Catalog to MongoDB ---")
        items = self._load_json()
        if not items:
            return

        collection = self.db[self.COLLECTION_NAME]

        # Ensure index
        try:
            collection.create_index("dataset_id", unique=True, background=True)
        except Exception as e:
            logger.warning(f"Could not create index: {e}")

        # Prepare bulk operations
        ops = []
        for item in items:
            # We sort sources to ensure deterministic comparison/storage if needed
            if "sources" in item:
                item["sources"].sort(key=self._get_sortable_key)

            ops.append(ReplaceOne({"dataset_id": item["dataset_id"]}, item, upsert=True))

        if not ops:
            return

        try:
            # Execute in batches
            for i in range(0, len(ops), self.DEFAULT_BATCH_SIZE):
                batch = ops[i : i + self.DEFAULT_BATCH_SIZE]
                res = collection.bulk_write(batch)
                logger.info(f"Batch {i}: Matched {res.matched_count}, Upserted {res.upserted_count}")
        except pymongo_errors.BulkWriteError as bwe:
            logger.error(f"Bulk write error: {bwe.details}")
        except Exception as e:
            logger.error(f"DB Load failed: {e}", exc_info=True)

        logger.info("--- Catalog Load Finished ---")
