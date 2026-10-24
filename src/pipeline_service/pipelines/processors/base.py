import logging
from abc import ABC, abstractmethod
from typing import Any

from pymongo.database import Database

from pipeline_service.database.mongo_loader import MongoLoader

logger = logging.getLogger(__name__)


class BaseETLProcessor(MongoLoader, ABC):
    """
    Abstract base class for local ETL (Extract, Transform, Load) processors.

    This class standardizes the workflow for processing local dataset files,
    providing database loading capabilities via inheritance.
    """

    def __init__(self, db: Database) -> None:
        """
        Initializes the processor with a database connection.

        Args:
            db: The MongoDB database instance.
        """
        super().__init__(db)

    @abstractmethod
    def _extract(self) -> Any:
        """
        Extracts raw data from the processor's source.

        Returns:
            The raw data object (usually a DataFrame).
        """
        pass

    @abstractmethod
    def _transform(self, raw_data: Any) -> Any:
        """
        Transforms raw data into a format suitable for the database.

        Args:
            raw_data: The output from the extraction step.

        Returns:
            A list of documents ready for insertion.
        """
        pass

    @abstractmethod
    def run(self) -> None:
        """
        Executes the full ETL pipeline: Extract -> Transform -> Load.
        """
        pass
