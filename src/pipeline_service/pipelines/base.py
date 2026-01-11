from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable, List, Optional

# Type alias for the progress callback function
# Arguments: progress (float 0.0 to 1.0), message (str)
ProgressCallback = Callable[[float, str], None]


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
        progress_callback: Optional[ProgressCallback] = None,
    ) -> None:
        """
        Executes the pipeline for the specified datasets and time range.

        Args:
            datasets: A list of dataset identifiers to process.
            start_time: The start of the time range (inclusive).
            end_time: The end of the time range (exclusive).
            progress_callback: Optional function to report execution progress.
                               Signature: (progress: float, message: str) -> None
        """
        pass
