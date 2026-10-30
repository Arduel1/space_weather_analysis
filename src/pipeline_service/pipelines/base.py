import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime

from pydantic import ValidationError
from pymongo.errors import PyMongoError

from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.jobs.models import ChunkGap
from space_weather_analysis.util.error_handling import DataProcessingError, DataRetrievalError

logger = logging.getLogger(__name__)

# Receives the progress of a run, from 0.0 to 1.0, and a status message.
ProgressCallback = Callable[[float, str], None]

# Failures of one chunk that leave a gap but let the run go on: a failed request, unreadable data or a failed write.
CHUNK_ERRORS = (DataRetrievalError, DataProcessingError, PyMongoError)

# Failures of a metadata save, which leave the stored data documents valid.
METADATA_SAVE_ERRORS = (PyMongoError, ValidationError, ValueError)


class BasePipeline(ABC):
    """The interface by which the orchestrator runs every data pipeline."""

    @abstractmethod
    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Ingests the datasets for a time range.

        Args:
            datasets: The IDs of the datasets to ingest.
            start_time: The start of the time range (inclusive).
            end_time: The end of the time range (exclusive).
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with the number of tried chunks and the chunks that failed.

        Raises:
            ValueError: If the request is invalid, so no chunk can be tried.
        """

    @staticmethod
    def _ingest_chunk[T](report: IngestionReport, dataset_id: str, start: datetime, stop: datetime, ingest: Callable[[], T]) -> T | None:
        """Ingests one chunk and records it as gap if it fails with one of CHUNK_ERRORS.

        Other exceptions are programming errors and propagate.

        Args:
            report: The report of the run, changed in place.
            dataset_id: The dataset ID.
            start: The start of the chunk (inclusive).
            stop: The end of the chunk (exclusive).
            ingest: Ingests the chunk.

        Returns:
            The result of ingest, or None if the chunk failed.
        """
        report.chunks_total += 1
        try:
            return ingest()
        except CHUNK_ERRORS as error:
            logger.warning("Chunk %s to %s of %s not ingested: %s", start, stop, dataset_id, error)
            report.gaps.append(ChunkGap(dataset_id=dataset_id, start=start, stop=stop, reason=f"{type(error).__name__}: {error}"))
            return None

    @staticmethod
    def _report_chunk_progress(
        progress_callback: ProgressCallback | None,
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
        except PyMongoError:
            logger.exception("Failed to refresh the coverage of %s", dataset_id)
            return
        logger.info("Refreshed coverage of %s: %d documents in %d intervals.", dataset_id, coverage.document_count, len(coverage.intervals))
