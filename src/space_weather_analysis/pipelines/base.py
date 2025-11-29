from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional


class BasePipeline(ABC):
    """
    Abstract base class defining the common interface for all data pipelines.
    """

    @abstractmethod
    def run(
        self,
        datasets: List[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> None:
        """
        Executes the pipeline for the specified datasets and time range.

        Args:
            datasets (List[str]): A list of dataset identifiers to process.
            start_time (Optional[datetime]): The start of the time range (inclusive).
            end_time (Optional[datetime]): The end of the time range (exclusive).
        """
        pass
