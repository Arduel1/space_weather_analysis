from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional


class BasePipeline(ABC):
    """
    Abstract base class defining the common interface for all data pipelines.

    This class enforces a consistent structure for executing data processing
    workflows across different data sources.
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
            datasets: A list of dataset identifiers to process.
            start_time: The start of the time range (inclusive).
            end_time: The end of the time range (exclusive).
        """
        pass
