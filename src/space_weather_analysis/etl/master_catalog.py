import json
import logging
import pathlib
from typing import Any, Dict, List, Optional, Tuple

from pymongo import ReplaceOne
from pymongo import errors as pymongo_errors
from pymongo.database import Database

from .base_processor import BaseETLProcessor

logger = logging.getLogger(__name__)


class CatalogETL(BaseETLProcessor):
    """
    ETL Processor for loading the master catalog file.
    This processor loads the catalog data into MongoDB, treating
    each dataset as a separate document.
    """

    COLLECTION_NAME = "master_catalog"
    DEFAULT_BATCH_SIZE = 1000

    def __init__(self, db: Database, file_path: pathlib.Path):
        """
        Initializes the Catalog ETL processor.

        Args:
            db (Database): The MongoDB database instance.
            file_path (pathlib.Path): Path to the catalog JSON file.
        """
        super().__init__(db)
        self.file_path = file_path

    def _extract(self) -> Optional[List[Dict[str, Any]]]:
        """
        Extracts master catalog data from the JSON file.

        The data is converted from its source dictionary format into a
        list of documents.

        Returns:
            Optional[List[Dict[str, Any]]]: A list of catalog documents,
                                            or None if extraction fails.
        """
        logger.info(f"--- Starting Extract for Master Catalog: {self.file_path} ---")
        if not self.file_path.exists():
            logger.error(f"Master catalog file not found at: {self.file_path}")
            return None

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                # Specify the exact structure loaded from JSON
                raw_data: Dict[str, List[Dict[str, Any]]] = json.load(f)

            # Convert dict to the list format required for loading
            catalog_list: List[Dict[str, Any]] = [
                {"dataset_id": dataset_id, "sources": sources} for dataset_id, sources in raw_data.items()
            ]

            logger.info(f"Extracted and parsed {len(catalog_list)} dataset entries from catalog.")
            return catalog_list

        except json.JSONDecodeError as e:
            logger.error(
                f"Error decoding JSON from {self.file_path}: {e}",
                exc_info=True,
            )
            return None
        except Exception as e:
            logger.error(f"Error reading master catalog file: {e}", exc_info=True)
            return None

    def _transform(self, raw_data: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """
        Transforms the catalog data into the final document structure.

        For this specific ETL, the data from _extract() is already in
        the correct format and is passed through directly.

        Args:
            raw_data (Optional[List[Dict[str, Any]]]): The data from _extract().

        Returns:
            List[Dict[str, Any]]: A list of formatted dictionaries (documents)
                                  for MongoDB.
        """
        if raw_data is None:
            logger.warning("Transform step skipped as no data was extracted.")
            return []

        # The data is already in the required format
        logger.info(f"Transformed {len(raw_data)} catalog documents.")
        return raw_data

    def _ensure_catalog_index(self) -> None:
        """
        Ensures a unique index exists on 'dataset_id' for fast lookups
        in the master_catalog collection.
        """
        try:
            collection = self.db[self.COLLECTION_NAME]
            collection.create_index("dataset_id", unique=True, background=True)
            logger.info(f"Ensured unique index on 'dataset_id' for {self.COLLECTION_NAME}.")
        except pymongo_errors.OperationFailure as e:
            logger.error(
                f"Failed to create index on {self.COLLECTION_NAME}: {e}",
                exc_info=True,
            )

    def _get_sortable_key_for_source(self, source: Dict[str, Any]) -> Tuple:
        """
        Creates a stable, sortable tuple representation of a source entry.
        """
        try:
            time_range = source.get("time_range", {})
            time_range_tuple = (
                time_range.get("start_time"),
                time_range.get("end_time"),
            )
            return (source.get("type"), source.get("source_id"), time_range_tuple)
        except Exception:
            # Fallback for malformed data
            return (source.get("type"), source.get("source_id"))

    def _load_catalog_data(self, documents: List[Dict[str, Any]]) -> None:
        """
        Handles the "Load" step by upserting catalog documents in batches.

        This method performs an in-memory comparison to find documents that
        are new or have semantically changed (ignoring source list order).
        Only new or modified documents are written to the database.

        Args:
            documents (List[Dict[str, Any]]): A list of catalog documents to upsert.
        """
        if not documents:
            logger.info(f"No documents to load for collection '{self.COLLECTION_NAME}'.")
            return

        collection = self.db[self.COLLECTION_NAME]

        # Fetch all existing documents into a map for quick lookup
        logger.info(f"Fetching existing catalog from {self.COLLECTION_NAME} for comparison...")
        try:
            existing_docs_cursor = collection.find({}, {"_id": 0})
            existing_docs_map: Dict[str, Dict[str, Any]] = {
                doc["dataset_id"]: doc for doc in existing_docs_cursor if "dataset_id" in doc
            }
            logger.info(f"Found {len(existing_docs_map)} existing catalog entries.")
        except Exception as e:
            logger.error(f"Failed to fetch existing catalog: {e}", exc_info=True)
            return

        logger.info(f"Comparing {len(documents)} new documents against existing ones to find changes...")

        bulk_operations: List[ReplaceOne] = []
        total_checked = 0
        total_unchanged = 0

        try:
            for doc in documents:
                total_checked += 1
                dataset_id = doc.get("dataset_id")
                if not dataset_id:
                    logger.warning(f"Skipping document with no 'dataset_id': {doc}")
                    continue

                existing_doc = existing_docs_map.get(dataset_id)

                if existing_doc is None:
                    # New document. Add to bulk operations.
                    bulk_operations.append(ReplaceOne({"dataset_id": dataset_id}, doc, upsert=True))
                    continue

                # Document exists. Perform comparison.
                # We normalize the 'sources' list by sorting it based on a stable key
                # to compare them semantically, ignoring list order.
                try:
                    sorted_existing_sources = sorted(
                        existing_doc.get("sources", []),
                        key=self._get_sortable_key_for_source,
                    )
                    sorted_new_sources = sorted(
                        doc.get("sources", []),
                        key=self._get_sortable_key_for_source,
                    )
                except Exception as e:
                    # Handle comparison errors (e.g., malformed data)
                    logger.warning(f"Could not compare sources for {dataset_id}, assuming different: {e}")
                    bulk_operations.append(ReplaceOne({"dataset_id": dataset_id}, doc, upsert=True))
                    continue

                if sorted_existing_sources != sorted_new_sources:
                    # Sources are different. Add to bulk operations.
                    bulk_operations.append(ReplaceOne({"dataset_id": dataset_id}, doc, upsert=True))
                else:
                    # Sources are identical. Do nothing.
                    total_unchanged += 1

                # Process in batches to avoid overwhelming the server
                if len(bulk_operations) >= self.DEFAULT_BATCH_SIZE:
                    result = collection.bulk_write(bulk_operations)
                    logger.info(
                        f"  ... processed batch (Inserted: {result.upserted_count}, Modified: {result.modified_count})"
                    )
                    bulk_operations.clear()

            # Process any remaining operations
            if bulk_operations:
                result = collection.bulk_write(bulk_operations)
                logger.info(
                    f"  ... processed final batch "
                    f"(Inserted: {result.upserted_count}, "
                    f"Modified: {result.modified_count})"
                )

            logger.info(
                f"Catalog data load complete for {self.COLLECTION_NAME}. "
                f"Total checked: {total_checked}, "
                f"Total unchanged: {total_unchanged}, "
                f"Total written (new or modified): {total_checked - total_unchanged}"
            )

        except pymongo_errors.BulkWriteError as bwe:
            logger.error(
                f"Error during bulk upsert into {self.COLLECTION_NAME}: {bwe.details}",
                exc_info=True,
            )
        except Exception as e:
            logger.error(
                f"Error loading data to MongoDB collection {self.COLLECTION_NAME}: {e}",
                exc_info=True,
            )

    def run(self) -> None:
        """
        Main orchestration method for the Catalog ETL process.

        This process is idempotent. It uses a bulk "upsert" operation
        to update existing dataset entries and insert new ones without
        deleting or modifying untouched entries.
        """
        logger.info("--- Starting ETL process for Master Catalog ---")

        raw_data = self._extract()
        documents = self._transform(raw_data)

        # This collection is not time-series, so we use a standard index
        self._ensure_catalog_index()

        # Use the custom upsert-based loader, not the time-series one
        self._load_catalog_data(documents)

        logger.info("--- Finished ETL process for Master Catalog ---")
