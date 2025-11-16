import json
import logging
import pathlib
from typing import Any, Dict, List, Optional

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

    def run(self) -> None:
        """
        Main orchestration method for the Catalog ETL process.
        """
        logger.info("--- Starting ETL process for Master Catalog ---")

        raw_data = self._extract()
        documents = self._transform(raw_data)

        # This collection is not time-series, so we use a standard index
        self._ensure_catalog_index()

        self._load_data(self.COLLECTION_NAME, documents, self.DEFAULT_BATCH_SIZE)

        logger.info("--- Finished ETL process for Master Catalog ---")
