from abc import abstractmethod
from typing import Any

from pymongo.database import Database

from space_weather_analysis.database.mongo_loader import MongoLoader
from space_weather_analysis.util.types import TransformedDocuments


class BaseETLProcessor(MongoLoader):
    """
    Abstract Base Class for an ETL processor.

    This class provides a standardized structure for ETL processes
    by defining the required abstract methods for extraction,
    transformation, and execution. It inherits common data loading
    functionality from its base class.
    """

    def __init__(self, db: Database):
        """
        Initializes the processor with a database connection.

        Args:
            db (Database): The MongoDB database instance.
        """
        super().__init__(db)

    @abstractmethod
    def _extract(self) -> Any:
        """
        Abstract method for the "Extract" step.

        This should read data from a source and return it in a
        structured format for the transform step.

        Returns:
            Any: The extracted data (e.g., a DataFrame, a list of dicts),
                 or None if extraction fails.
        """
        pass

    @abstractmethod
    def _transform(self, raw_data: Any) -> TransformedDocuments:
        """
        Abstract method for the "Transform" step.

        This should clean and format the raw data (from _extract) into
        a list of documents suitable for database insertion.

        Args:
            raw_data (Any): The raw data from _extract().

        Returns:
            TransformedDocuments: A list of formatted dictionaries.
        """
        pass

    @abstractmethod
    def run(self) -> None:
        """
        Main orchestration method for the ETL process.

        This method is responsible for executing the complete
        extract, transform, and load sequence for the processor.
        """
        pass
