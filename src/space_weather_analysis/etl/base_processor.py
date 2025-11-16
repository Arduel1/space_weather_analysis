import logging
from abc import abstractmethod
from typing import Any, Dict, List, Optional

import pandas as pd
from pymongo.database import Database


from space_weather_analysis.database.mongo_loader_base import MongoLoaderBase

logger = logging.getLogger(__name__)


class BaseETLProcessor(MongoLoaderBase):
    """
    Abstract Base Class for an ETL processor.

    This class provides a standardized structure for all ETL processes.
    It handles the "Load" step via inheritance from MongoLoaderBase.

    A child class MUST implement:
    - _extract(): To fetch raw data (e.g., from a file).
    - _transform(): To clean and structure the data.
    - run(): The main orchestration method.
    """

    def __init__(self, db: Database):
        """
        Initializes the processor with a database connection.

        Args:
            db (Database): The MongoDB database instance.
        """
        super().__init__(db)  # <-- Call super()__init__

    @abstractmethod
    def _extract(self) -> Optional[pd.DataFrame]:
        """
        Abstract method for the "Extract" step.
        This should read data from a source (file, API, etc.).

        Returns:
            Optional[pd.DataFrame]: A DataFrame containing the raw data,
                                    or None if extraction fails.
        """
        pass

    @abstractmethod
    def _transform(self, raw_data: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
        """
        Abstract method for the "Transform" step.
        This should clean and format the raw data into a list of documents.

        Args:
            raw_data (Optional[pd.DataFrame]): The raw data from _extract().

        Returns:
            List[Dict[str, Any]]: A list of formatted dictionaries (documents)
                                  for MongoDB.
        """
        pass

    @abstractmethod
    def run(self):
        """
        Main orchestration method for the ETL process.
        This method should call _extract, _transform, and _load.
        """
        pass
