import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd
from pymongo.database import Database

from pipeline_service.database.mongo_loader import MongoLoader

logger = logging.getLogger(__name__)


class BaseETLProcessor(MongoLoader, ABC):
    """Abstract base class for local ETL (Extract, Transform, Load) processors.

    This class standardizes the workflow for processing local dataset files,
    providing database loading capabilities via inheritance.
    """

    def __init__(self, db: Database) -> None:
        """Initializes the processor with a database connection.

        Args:
            db: The MongoDB database instance.
        """
        super().__init__(db)

    @abstractmethod
    def _extract(self) -> Any:
        """Extracts raw data from the processor's source.

        Returns:
            The raw data object (usually a DataFrame).
        """
        pass

    @abstractmethod
    def _transform(self, raw_data: Any) -> Any:
        """Transforms raw data into a format suitable for the database.

        Args:
            raw_data: The output from the extraction step.

        Returns:
            A list of documents ready for insertion.
        """
        pass

    @staticmethod
    def _build_documents(timestamps: List[datetime], rows: List[Dict[str, Any]], metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Combines timestamps and data rows into time series documents.

        Missing values (NaN, NA, None) are left out of a document's data instead of being stored.

        Args:
            timestamps: Time of each row.
            rows: Field values of each row, in the same order as timestamps.
            metadata: Metadata stored with every document.

        Returns:
            One document per row with the fields timestamp, metadata and data.
        """
        documents = []
        for timestamp, row in zip(timestamps, rows, strict=True):
            present_fields = {field: value for field, value in row.items() if not pd.isna(value)}
            documents.append({"timestamp": timestamp, "metadata": metadata, "data": present_fields})
        return documents

    @abstractmethod
    def run(self) -> None:
        """Executes the full ETL pipeline: Extract -> Transform -> Load."""
        pass
