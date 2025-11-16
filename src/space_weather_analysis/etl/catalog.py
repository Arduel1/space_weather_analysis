import json
import logging
import pathlib
from typing import Any, Dict, List, Optional

import pandas as pd
from pymongo import errors as pymongo_errors
from pymongo.database import Database

from .base_processor import BaseETLProcessor

logger = logging.getLogger(__name__)


class CatalogETL(BaseETLProcessor):
    """
    ETL Processor for the master_catalog.json file.
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
            file_path (pathlib.Path): Path to the 'master_catalog.json' file.
        """
        super().__init__(db)
        self.file_path = file_path

    def _extract(self) -> Optional[pd.DataFrame]:
        """
        Extracts the master catalog data from the JSON file.

        The JSON is a dictionary {dataset_id: [sources...]}
        This is converted to a DataFrame with columns ['dataset_id', 'sources'].

        Returns:
            Optional[pd.DataFrame]: A DataFrame where each row contains
                                    a 'dataset_id' and its 'sources' list.
        """
        logger.info(f"--- Starting Extract for Master Catalog: {self.file_path} ---")
        if not self.file_path.exists():
            logger.error(f"Master catalog file not found at: {self.file_path}")
            return None

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw_data: Dict[str, Any] = json.load(f)

            # Convert the dict {dataset_id: [sources...]} to a list of dicts
            # for easy conversion to a DataFrame.
            catalog_list = [{"dataset_id": dataset_id, "sources": sources} for dataset_id, sources in raw_data.items()]

            df = pd.DataFrame(catalog_list)
            logger.info(f"Extracted and parsed {len(df)} dataset entries from catalog.")
            return df

        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {self.file_path}: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Error reading master catalog file: {e}", exc_info=True)
            return None

    def _transform(self, raw_data: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
        """
        Transforms the catalog DataFrame into MongoDB documents.

        Args:
            raw_data (Optional[pd.DataFrame]): The raw data from _extract().

        Returns:
            List[Dict[str, Any]]: A list of formatted dictionaries (documents)
                                  for MongoDB. Each document has the structure:
                                  { 'dataset_id': str, 'sources': List[Dict] }
        """
        if raw_data is None:
            logger.warning("Transform step skipped as no data was extracted.")
            return []

        logger.info("--- Starting Transform for Master Catalog ---")

        # The DataFrame is already in the correct structure,
        # just convert it to a list of records.
        documents = raw_data.to_dict("records")

        logger.info(f"Transformed {len(documents)} catalog documents.")
        return documents

    def _ensure_catalog_index(self):
        """
        Ensures a unique index exists on 'dataset_id' for fast lookups
        in the master_catalog collection.
        """
        try:
            collection = self.db[self.COLLECTION_NAME]
            collection.create_index("dataset_id", unique=True, background=True)
            logger.info(f"Ensured unique index on 'dataset_id' for {self.COLLECTION_NAME}.")
        except pymongo_errors.OperationFailure as e:
            logger.error(f"Failed to create index on {self.COLLECTION_NAME}: {e}", exc_info=True)

    def run(self):
        """
        Main orchestration method for the Catalog ETL process.

        This process is idempotent. It clears the existing collection
        and re-inserts all data from the JSON file.
        """
        logger.info("--- Starting ETL process for Master Catalog ---")

        # Extract
        raw_data_df = self._extract()

        # Transform
        documents = self._transform(raw_data_df)

        # Load
        # We ensure a standard index, not a time-series collection.
        self._ensure_catalog_index()

        # Load data using the inherited method
        # This will clear the collection before loading.
        self._load_data(self.COLLECTION_NAME, documents, self.DEFAULT_BATCH_SIZE)

        logger.info("--- Finished ETL process for Master Catalog ---")
