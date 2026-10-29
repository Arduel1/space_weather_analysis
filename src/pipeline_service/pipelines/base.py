import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable, List, Optional

from space_weather_analysis.coverage.repository import CoverageRepository

logger = logging.getLogger(__name__)

# Type alias for the progress callback function
# Arguments: progress (float 0.0 to 1.0), message (str)
ProgressCallback = Callable[[float, str], None]


class BasePipeline(ABC):
    """Abstract base class defining the common interface for all data pipelines.

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
        """Executes the pipeline for the specified datasets and time range.

        Args:
            datasets: A list of dataset identifiers to process.
            start_time: The start of the time range (inclusive).
            end_time: The end of the time range (exclusive).
            progress_callback: Optional function to report execution progress.
                               Signature: (progress: float, message: str) -> None
        """
        pass

    @staticmethod
    def _report_chunk_progress(
        progress_callback: Optional[ProgressCallback],
        dataset_index: int,
        dataset_count: int,
        chunk_index: int,
        chunk_count: int,
        message: str,
    ) -> None:
        """Reports the progress at the start of a chunk, if a callback is given.

        Each dataset takes an equal share of the total progress, which its chunks divide equally.

        Args:
            progress_callback: The function receiving the progress, or None to report nothing.
            dataset_index: The zero-based index of the current dataset.
            dataset_count: The number of datasets in the run.
            chunk_index: The zero-based index of the current chunk within the dataset.
            chunk_count: The number of chunks of the dataset.
            message: The status message passed along with the progress.
        """
        if progress_callback is None:
            return
        dataset_base_progress = dataset_index / dataset_count
        chunk_progress = chunk_index / chunk_count
        progress_callback(dataset_base_progress + (chunk_progress / dataset_count), message)

    @staticmethod
    def _refresh_coverage(coverage_repository: CoverageRepository, dataset_id: str, collection_name: str) -> None:
        """Recomputes and saves which time ranges of a dataset are stored.

        A failing refresh is logged, as the stored data documents stay valid without coverage.

        Args:
            coverage_repository: The repository computing and storing the coverage.
            dataset_id: The dataset ID.
            collection_name: The collection holding the data documents of the dataset.
        """
        try:
            coverage = coverage_repository.refresh(dataset_id, collection_name)
            logger.info(f"Refreshed coverage of {dataset_id}: {coverage.document_count} documents in {len(coverage.intervals)} intervals.")
        except Exception as e:
            logger.error(f"Failed to refresh coverage of {dataset_id}: {e}", exc_info=True)
