import logging
import pathlib
from abc import ABC, abstractmethod
from typing import ClassVar

import pandas as pd
from pymongo.database import Database

from pipeline_service.database.mongo_loader import MongoLoader
from pipeline_service.util.documents import DocumentDict
from space_weather_analysis.util.error_handling import DataProcessingError

logger = logging.getLogger(__name__)


class BaseETLProcessor(MongoLoader, ABC):
    """Base of the processors that load a complete local dataset file into its own collection.

    The file holds the whole dataset, so every run replaces the content of the collection; the
    documents are built completely before the stored ones are deleted.

    Attributes:
        COLLECTION_NAME: The collection holding the documents of the dataset.
    """

    COLLECTION_NAME: ClassVar[str]

    def __init__(self, db: Database, file_path: pathlib.Path) -> None:
        """Initializes the processor.

        Args:
            db: The MongoDB database instance.
            file_path: The path of the dataset file.
        """
        super().__init__(db)
        self.file_path = file_path

    @abstractmethod
    def _extract(self) -> pd.DataFrame:
        """Reads the dataset file.

        Returns:
            The raw rows of the file.

        Raises:
            FileNotFoundError: If the file does not exist.
            DataProcessingError: If the file cannot be read.
        """

    @abstractmethod
    def _transform(self, raw_data: pd.DataFrame) -> list[DocumentDict]:
        """Builds the time series documents of the raw rows.

        Args:
            raw_data: The raw rows of the file.

        Returns:
            One document per row with a valid time stamp.
        """

    def run(self) -> int:
        """Replaces the content of the collection with the documents built from the whole file.

        Returns:
            The number of stored documents.

        Raises:
            FileNotFoundError: If the file does not exist.
            DataProcessingError: If the file cannot be read or holds no valid row, so the stored documents are kept.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        documents = self._transform(self._extract())
        if not documents:
            raise DataProcessingError(f"{self.file_path} holds no row with a valid time stamp; the stored documents are kept.")

        self._ensure_timeseries_collection(self.COLLECTION_NAME)
        self._load_data(self.COLLECTION_NAME, documents, clear_collection=True)
        logger.info("Replaced the content of %s with %d documents.", self.COLLECTION_NAME, len(documents))
        return len(documents)
