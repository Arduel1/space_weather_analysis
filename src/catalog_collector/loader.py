import json
import logging
import pathlib
from typing import Any, Dict, List, Optional, Tuple

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
    METADATA_COLLECTION_NAME = "dataset_metadata"
    DEFAULT_BATCH_SIZE = 1000

    def __init__(
        self,
        db: Database,
        file_path: pathlib.Path,
        metadata_path: Optional[pathlib.Path] = None,
    ):
        """
        Initializes the Catalog Loader.

        Args:
            db (Database): The MongoDB database instance.
            file_path (pathlib.Path): Path to the catalog JSON file.
            metadata_path (Optional[pathlib.Path]): Path to the metadata JSON file.
        """
        self.db = db
        self.file_path = file_path
        self.metadata_path = metadata_path

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
        logger.info(f"--- Loading Catalog Cache from {self.file_path} to MongoDB ---")
        items = self._load_json()
        if not items:
            logger.warning("Catalog file is empty or missing. Starting with empty DB.")
            return
        logger.info(f"Found {len(items)} datasets in catalog file.")

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

    def load_metadata_to_db(self) -> None:
        """
        Loads the metadata catalog from file to MongoDB.
        """
        if not self.metadata_path or not self.metadata_path.exists():
            logger.warning(f"Metadata file not found or not set: {self.metadata_path}")
            return

        logger.info(f"--- Loading Metadata Cache from {self.metadata_path} to MongoDB ---")
        try:
            with open(self.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not data:
                logger.warning("Metadata file is empty.")
                return

            collection = self.db[self.METADATA_COLLECTION_NAME]
            try:
                collection.create_index("dataset_id", unique=True, background=True)
            except Exception as e:
                logger.warning(f"Could not create index on metadata: {e}")

            ops = []
            if isinstance(data, list):
                for item in data:
                    dataset_id = item.get("dataset_id") or item.get("id")
                    if not dataset_id:
                        continue

                    # Check if the structure is already {dataset_id: ..., metadata: ...}
                    if "metadata" in item:
                        meta_content = item["metadata"]
                    else:
                        # Treat the entire item as metadata
                        meta_content = item

                    doc = {"dataset_id": dataset_id, "metadata": meta_content}
                    ops.append(ReplaceOne({"dataset_id": dataset_id}, doc, upsert=True))

            elif isinstance(data, dict):
                for dataset_id, meta_content in data.items():
                    doc = {"dataset_id": dataset_id, "metadata": meta_content}
                    ops.append(ReplaceOne({"dataset_id": dataset_id}, doc, upsert=True))

            for i in range(0, len(ops), self.DEFAULT_BATCH_SIZE):
                batch = ops[i : i + self.DEFAULT_BATCH_SIZE]
                res = collection.bulk_write(batch)
                logger.info(f"Metadata Batch {i}: Matched {res.matched_count}, Upserted {res.upserted_count}")

        except Exception as e:
            logger.error(f"Failed to load metadata file: {e}", exc_info=True)

        logger.info("--- Metadata Load Finished ---")

    def upsert_dataset(self, dataset_id: str, sources: List[CatalogEntryDict]) -> None:
        """
        Upserts a single dataset entry into MongoDB.
        """
        collection = self.db[self.COLLECTION_NAME]

        # Sort sources to ensure deterministic storage
        sorted_sources = sorted(sources, key=self._get_sortable_key)

        item = {"dataset_id": dataset_id, "sources": sorted_sources}

        try:
            result = collection.replace_one({"dataset_id": dataset_id}, item, upsert=True)
            action = "Created" if result.upserted_id else "Updated"
            logger.info(f"DB {action}: {dataset_id} (Sources: {len(sorted_sources)})")
        except Exception as e:
            logger.error(f"Failed to upsert dataset {dataset_id}: {e}")

    def delete_dataset(self, dataset_id: str) -> None:
        """
        Deletes a dataset from the MongoDB collection.
        """
        collection = self.db[self.COLLECTION_NAME]
        try:
            result = collection.delete_one({"dataset_id": dataset_id})
            if result.deleted_count > 0:
                logger.info(f"DB Deleted: {dataset_id}")
            else:
                logger.info(f"DB Delete skipped (not found): {dataset_id}")
        except Exception as e:
            logger.error(f"Failed to delete dataset {dataset_id}: {e}")

    def upsert_metadata(self, dataset_id: str, metadata: Dict[str, Any]) -> None:
        """
        Upserts metadata for a dataset into the metadata collection.
        """
        collection = self.db[self.METADATA_COLLECTION_NAME]
        item = {"dataset_id": dataset_id, "metadata": metadata}

        try:
            # replace_one checks for changes if the document exists
            result = collection.replace_one({"dataset_id": dataset_id}, item, upsert=True)
            if result.upserted_id:
                logger.info(f"Metadata Created: {dataset_id}")
            elif result.modified_count > 0:
                logger.info(f"Metadata Updated: {dataset_id}")
        except Exception as e:
            logger.error(f"Failed to upsert metadata for {dataset_id}: {e}")

    def delete_metadata(self, dataset_id: str) -> None:
        """
        Deletes metadata for a dataset.
        """
        collection = self.db[self.METADATA_COLLECTION_NAME]
        try:
            result = collection.delete_one({"dataset_id": dataset_id})
            if result.deleted_count > 0:
                logger.info(f"Metadata Deleted: {dataset_id}")
        except Exception as e:
            logger.error(f"Failed to delete metadata for {dataset_id}: {e}")
