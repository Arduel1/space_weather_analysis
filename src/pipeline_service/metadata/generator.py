import logging
from typing import Any, Dict, List, Optional

import numpy as np
from pymongo.database import Database

from pipeline_service.metadata.models import VariableMetadata
from pipeline_service.util.documents import build_metadata_document

logger = logging.getLogger(__name__)


class MetadataWriter:
    """Creates and saves standardized metadata documents to MongoDB.

    This class handles the normalization of Python/NumPy types into
    BSON-compatible formats suitable for MongoDB storage.
    """

    COLLECTION_NAME = "dataset_metadata"

    def __init__(self, db: Database) -> None:
        """Initializes the writer with a database connection.

        Args:
            db: The MongoDB database instance.
        """
        self.db = db
        self.collection = self.db[self.COLLECTION_NAME]

    def _sanitize_value(self, value: Any) -> Any:
        """Recursively sanitizes a value to make it BSON-compatible.

        Handles conversion of:
        - Dictionaries and Lists (recursive)
        - NumPy arrays -> Lists
        - NumPy scalars (floats/ints/bools) -> Python natives
        - NaN/Inf -> None
        - Bytes -> UTF-8 Strings

        Args:
            value: The value to sanitize.

        Returns:
            A BSON-compatible value (dict, list, str, int, float, bool, or None).
        """
        if isinstance(value, dict):
            return {k: self._sanitize_value(v) for k, v in value.items()}

        if isinstance(value, list):
            return [self._sanitize_value(v) for v in value]

        if isinstance(value, np.ndarray):
            # Convert numpy array to list and sanitize elements
            return [self._sanitize_value(v) for v in value.tolist()]

        # Handle NumPy/Float Special Values (NaN/Inf)
        if isinstance(value, (np.floating, float)):
            if np.isnan(value) or np.isinf(value):
                return None  # MongoDB uses null for missing values
            return float(value)

        # Handle other NumPy types
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.bool_):
            return bool(value)

        # Handle Bytes
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")

        # Return the value if it's already a compatible type (str, int, bool, None)
        return value

    def save_metadata_to_db(
        self,
        dataset_id: str,
        variables: List[VariableMetadata],
        source_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Combines metadata and upserts it into the MongoDB collection.

        The dataset_id is used as the MongoDB `_id` field to ensure uniqueness
        per dataset.

        Args:
            dataset_id: The unique identifier of the dataset (used as _id).
            variables: A list of standardized variable metadata objects.
            source_info: Optional descriptive info about the source.
        """
        logger.info(f"Saving metadata document for {dataset_id} to MongoDB...")

        if not variables:
            logger.warning(f"No variable metadata provided for {dataset_id}. Skipping save.")
            return

        try:
            # Sanitize Source Info
            clean_source_info = self._sanitize_value(source_info) if source_info else {}

            # Serialize and Sanitize Variables
            variable_docs: List[Dict[str, Any]] = []
            # Sort by name for consistent document structure
            for var in sorted(variables, key=lambda v: v.name):
                # We construct the dict manually to ensure strict schema adherence
                # and apply sanitization specifically to the file_attributes dict.
                sanitized_attrs = self._sanitize_value(var.file_attributes)

                var_doc = {
                    "name": var.name,
                    "description": var.description,
                    "units": var.units,
                    "fill": var.fill,
                    "type": var.type,
                    "size": var.size,
                    "file_attributes": sanitized_attrs,
                }
                variable_docs.append(var_doc)

            # Construct Final Document
            metadata_document = build_metadata_document(dataset_id, clean_source_info, variable_docs)

            # Upsert (Insert or Update)
            self.collection.update_one({"_id": dataset_id}, {"$set": metadata_document}, upsert=True)

            logger.info(f"Successfully saved metadata for {dataset_id} to collection '{self.COLLECTION_NAME}'")

        except Exception as e:
            logger.error(f"Failed to write metadata document to MongoDB: {e}", exc_info=True)
