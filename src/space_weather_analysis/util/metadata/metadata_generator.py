import logging
from typing import Any, Dict, List, Optional

import numpy as np
from pymongo.database import Database

from space_weather_analysis.util.metadata.variable_metadata import VariableMetadata

logger = logging.getLogger(__name__)


class MetadataWriter:
    """
    Creates and saves standardized metadata documents to MongoDB.
    """

    COLLECTION_NAME = "dataset_metadata"

    def __init__(self, db: Database):
        """
        Initializes the writer with a database connection.

        Args:
            db (Database): The MongoDB database instance.
        """
        self.db = db
        self.collection = self.db[self.COLLECTION_NAME]

    def _sanitize_value(self, value: Any) -> Any:
        """
        Recursively sanitizes a value to make it BSON-compatible.

        - Converts numpy integers/floats to standard python int/float.
        - Converts numpy bools to standard python bool.
        - Converts np.nan and np.inf to None.
        - Decodes bytes to utf-8 strings.

        Args:
            value (Any): The value to sanitize.

        Returns:
            Any: A BSON-compatible value.
        """
        if isinstance(value, dict):
            # Recurse into dictionaries
            return {k: self._sanitize_value(v) for k, v in value.items()}
        if isinstance(value, list):
            # Recurse into lists
            return [self._sanitize_value(v) for v in value]

        # --- Handle NumPy/Float Special Values (NaN/Inf) ---
        if isinstance(value, (np.floating, float)):
            if np.isnan(value) or np.isinf(value):
                return None  # Convert NaN/Inf to None (BSON null)
            return float(value)  # Convert np.float32 -> float

        # --- Handle other NumPy types ---
        if isinstance(value, np.integer):
            return int(value)  # Convert np.int32/64 -> int
        if isinstance(value, np.bool_):
            return bool(value)  # Convert np.bool_ -> bool

        # --- Handle Bytes ---
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")

        # Return the value if it's already a compatible type
        return value

    def save_metadata_to_db(
        self,
        dataset_id: str,
        variables: List[VariableMetadata],
        source_info: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Combines metadata and upserts it into the MongoDB collection.

        Args:
            dataset_id (str): The ID of the dataset. This will be used
                              as the '_id' for the MongoDB document.
            variables (List[VariableMetadata]): A list of standardized
                                                variable metadata objects.
            source_info (Optional[Dict[str, str]]): Optional header info,
                                                   such as source server or citation.
        """
        logger.info(f"Saving metadata document for {dataset_id} to MongoDB...")

        if not variables:
            logger.warning(f"No variable metadata provided for {dataset_id}. Skipping save.")
            return

        try:
            # Convert VariableMetadata objects to simple dicts for serialization
            variable_docs = []
            for var in sorted(variables, key=lambda v: v.name):
                sanitized_attrs = self._sanitize_value(var.file_attributes)
                variable_docs.append(
                    {
                        "name": var.name,
                        "description": var.description,
                        "units": var.units,
                        "fill": var.fill,
                        "type": var.type,
                        "size": var.size,
                        "file_attributes": sanitized_attrs,  # Use sanitized dict
                    }
                )

            # Create the complete document
            metadata_document = {
                "_id": dataset_id,
                "source_info": source_info or {},
                "variables": variable_docs,
            }

            # Use update_one with upsert=True to create or replace
            self.collection.update_one({"_id": dataset_id}, {"$set": metadata_document}, upsert=True)

            logger.info(f"Successfully saved metadata for {dataset_id} to collection '{self.COLLECTION_NAME}'")

        except Exception as e:
            logger.error(f"Failed to write metadata document to MongoDB: {e}", exc_info=True)
