import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from functools import partial
from typing import Protocol

from pydantic import ValidationError
from pymongo.errors import PyMongoError

from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.jobs.models import ChunkGap
from space_weather_analysis.timeseries.documents import TIME_FIELD
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.error_handling import DataProcessingError, DataRetrievalError
from space_weather_analysis.util.types import TimeRange

logger = logging.getLogger(__name__)

# Failures of one chunk that leave a gap but let the run go on: a failed request, unreadable data or a failed write.
CHUNK_ERRORS = (DataRetrievalError, DataProcessingError, PyMongoError)

# Failures of a metadata save, which leave the stored data documents valid.
METADATA_SAVE_ERRORS = (PyMongoError, ValidationError, ValueError)


class ChunkSource(Protocol):
    """The source-specific steps of a run that ingests datasets chunk by chunk.

    An instance serves one run and may collect what the chunks of a dataset delivered until save_metadata.
    """

    @property
    def granularity(self) -> Granularity:
        """The granularity of the time series collections, the unit closest to the spacing of the documents."""

    def collection_for(self, dataset_id: str) -> str:
        """Names the collection holding the data documents of a dataset.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The collection name.
        """

    def should_skip(self, dataset_id: str, collection: str, start: datetime, stop: datetime) -> bool:
        """Tells whether the stored documents of a chunk need no request.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
            start: The start of the chunk (inclusive).
            stop: The end of the chunk (exclusive).

        Returns:
            True if the chunk is skipped.
        """

    def ingest_chunk(self, dataset_id: str, collection: str, start: datetime, stop: datetime) -> bool:
        """Fetches one chunk and stores its documents.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
            start: The start of the chunk (inclusive).
            stop: The end of the chunk (exclusive).

        Returns:
            True if the provider delivered data for the chunk.

        Raises:
            DataRetrievalError: If the request failed.
            DataProcessingError: If the delivered data cannot be read.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """

    def save_metadata(self, dataset_id: str, collection: str) -> None:
        """Saves the metadata record built from what the chunks of a dataset delivered; nothing if they delivered nothing.

        A failing save is logged, as the stored data documents stay valid without metadata.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
        """


def run_chunked(
    source: ChunkSource,
    time_ranges: Mapping[str, TimeRange],
    *,
    chunk_duration: timedelta,
    timeseries: TimeSeriesRepository,
    coverage_repository: CoverageRepository,
    progress_callback: ProgressCallback | None = None,
) -> IngestionReport:
    """Ingests each dataset over its time range in chunks of the chunk duration.

    A chunk that fails with one of CHUNK_ERRORS becomes a gap and the run goes on. After the chunks of a
    dataset its metadata is saved, and its coverage is refreshed if at least one chunk delivered data.

    Args:
        source: The source-specific steps of the run.
        time_ranges: The time range of each dataset, keyed by dataset ID, in the order of the run.
        chunk_duration: The time span of one chunk.
        timeseries: The repository creating the time series collections.
        coverage_repository: The repository computing and storing the coverage.
        progress_callback: Receives the progress of the run; None to report nothing.

    Returns:
        The report with the number of tried chunks and the chunks that failed.
    """
    report = IngestionReport()
    for dataset_index, (dataset_id, (start, stop)) in enumerate(time_ranges.items()):
        time_chunks = generate_time_chunks(start, stop, chunk_duration)
        collection = source.collection_for(dataset_id)
        logger.info("Dataset %s: ingesting %s to %s in %d chunks", dataset_id, start, stop, len(time_chunks))
        timeseries.ensure_collection(collection, source.granularity)

        delivered_any_chunk = False
        for chunk_index, (chunk_start, chunk_stop) in enumerate(time_chunks):
            if source.should_skip(dataset_id, collection, chunk_start, chunk_stop):
                logger.info("Skipping chunk %s to %s of %s: the stored documents need no update", chunk_start, chunk_stop, dataset_id)
            else:
                ingest = partial(source.ingest_chunk, dataset_id, collection, chunk_start, chunk_stop)
                if ingest_or_record_gap(report, dataset_id, chunk_start, chunk_stop, ingest):
                    delivered_any_chunk = True
            _report_chunk_progress(
                progress_callback, dataset_id, dataset_position=(dataset_index, len(time_ranges)), chunks_done=(chunk_index + 1, len(time_chunks))
            )

        source.save_metadata(dataset_id, collection)
        if delivered_any_chunk:
            refresh_coverage(coverage_repository, dataset_id, collection)
    return report


def generate_time_chunks(start_time: datetime, stop_time: datetime, chunk_delta: timedelta) -> list[TimeRange]:
    """Splits a time range into consecutive chunks of a fixed length; the last chunk ends at the stop time.

    Args:
        start_time: The start of the range (inclusive).
        stop_time: The end of the range (exclusive).
        chunk_delta: The length of each chunk.

    Returns:
        The (chunk_start, chunk_stop) pairs in time order.
    """
    chunks = []
    current_start = start_time
    while current_start < stop_time:
        current_stop = min(current_start + chunk_delta, stop_time)
        chunks.append((current_start, current_stop))
        current_start = current_stop
    return chunks


def ingest_or_record_gap[T](report: IngestionReport, dataset_id: str, start: datetime, stop: datetime, ingest: Callable[[], T]) -> T | None:
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


def refresh_coverage(coverage_repository: CoverageRepository, dataset_id: str, collection: str) -> None:
    """Recomputes and saves which time ranges of a dataset are stored.

    A failing refresh is logged, as the stored data documents stay valid without coverage.

    Args:
        coverage_repository: The repository computing and storing the coverage.
        dataset_id: The dataset ID.
        collection: The collection holding the data documents of the dataset.
    """
    try:
        coverage = coverage_repository.refresh(dataset_id, collection, time_field=TIME_FIELD)
    except PyMongoError:
        logger.exception("Failed to refresh the coverage of %s", dataset_id)
        return
    logger.info("Refreshed coverage of %s: %d documents in %d intervals", dataset_id, coverage.document_count, len(coverage.intervals))


def _report_chunk_progress(
    progress_callback: ProgressCallback | None,
    dataset_id: str,
    *,
    dataset_position: tuple[int, int],
    chunks_done: tuple[int, int],
) -> None:
    """Reports the progress at the end of a chunk, if a callback is given.

    Each dataset takes an equal share of the total progress, which its chunks divide equally.

    Args:
        progress_callback: The function receiving the progress, or None to report nothing.
        dataset_id: The ID of the current dataset, named in the status message.
        dataset_position: The zero-based index of the current dataset and the number of datasets in the run.
        chunks_done: The number of finished chunks of the dataset and the number of its chunks.
    """
    if progress_callback is None:
        return
    dataset_index, dataset_count = dataset_position
    finished_chunks, chunk_count = chunks_done
    progress = (dataset_index + finished_chunks / chunk_count) / dataset_count
    progress_callback(progress, f"Dataset {dataset_id}: chunk {finished_chunks}/{chunk_count} done")
