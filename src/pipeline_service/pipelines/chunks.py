import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import NamedTuple, Protocol

from pydantic import ValidationError
from pymongo.errors import PyMongoError

from pipeline_service.chunk_status.models import ChunkStatus
from pipeline_service.chunk_status.repository import ChunkStatusRepository
from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.jobs.models import ChunkGap
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.timeseries.documents import TIME_FIELD
from space_weather_analysis.timeseries.models import Granularity, TimeBucket
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.error_handling import DataProcessingError, DataRetrievalError, SourceUnreachableError
from space_weather_analysis.util.time_utils import parse_iso_duration
from space_weather_analysis.util.types import TimeRange

logger = logging.getLogger(__name__)

# Failures of one chunk that leave a gap but let the run go on: a failed request, unreadable data or a failed write.
CHUNK_ERRORS = (DataRetrievalError, DataProcessingError, PyMongoError)

# Failures of a metadata save, which leave the stored data documents valid.
METADATA_SAVE_ERRORS = (PyMongoError, ValidationError, ValueError)

# Time after the end of a fetched range from which data of an archive or real-time feed count as stable;
# real-time feeds add late records and replace preliminary ones within hours after the measurement.
SETTLE_DELAY = timedelta(days=2)

# Largest cadence (time between records) per calendar cell size, so that one request holds at most about 50,000 records:
# a day of 1-second data has 86,400, a month of 1-minute data 44,640, a year of hourly data 8,784.
_DAY_CELL_MAX_CADENCE = timedelta(minutes=1)
_MONTH_CELL_MAX_CADENCE = timedelta(hours=1)


class GridChunk(NamedTuple):
    """The part of one cell of the calendar grid that a run requests.

    Attributes:
        cell_start: The start of the grid cell, which identifies the chunk.
        start: The start of the requested part (inclusive).
        stop: The end of the requested part (exclusive).
    """

    cell_start: datetime
    start: datetime
    stop: datetime


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

    def chunk_unit(self, dataset_id: str) -> TimeBucket:
        """Names the calendar unit of the grid cells a dataset is fetched in.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The cell unit.
        """

    def is_stable(self, dataset_id: str, collection: str, start: datetime, stop: datetime, fetched_at: datetime) -> bool:
        """Tells whether freshly fetched data of a chunk are unlikely to change, so that a new fetch can wait for the recheck.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
            start: The start of the fetched range (inclusive).
            stop: The end of the fetched range (exclusive).
            fetched_at: When the fetch finished.

        Returns:
            True if the fetched data are stable.

        Raises:
            pymongo.errors.PyMongoError: If the stored documents cannot be read.
        """

    def ingest_chunk(self, dataset_id: str, collection: str, start: datetime, stop: datetime) -> bool:
        """Fetches one chunk and replaces its stored documents; stored documents stay if the chunk delivers no data.

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
    timeseries: TimeSeriesRepository,
    coverage_repository: CoverageRepository,
    chunk_status: ChunkStatusRepository,
    progress_callback: ProgressCallback | None = None,
) -> IngestionReport:
    """Ingests each dataset over its time range in chunks on a calendar grid, skipping chunks whose last fetch is current.

    A fetched chunk records its status; a chunk that fails with one of CHUNK_ERRORS records none, becomes a gap and
    the run goes on. Once the provider of a dataset sent no answer at all, the remaining chunks of that dataset become
    gaps without a request. After the chunks of a dataset its metadata is saved, and its coverage is refreshed if at
    least one chunk delivered data.

    Args:
        source: The source-specific steps of the run.
        time_ranges: The time range of each dataset, keyed by dataset ID, in the order of the run.
        timeseries: The repository creating the time series collections.
        coverage_repository: The repository computing and storing the coverage.
        chunk_status: The repository storing the status of each fetched chunk.
        progress_callback: Receives the progress of the run; None to report nothing.

    Returns:
        The report with the number of tried chunks and the chunks that failed.
    """
    report = IngestionReport()
    for dataset_index, (dataset_id, (start, stop)) in enumerate(time_ranges.items()):
        unit = source.chunk_unit(dataset_id)
        grid_chunks = generate_time_chunks(start, stop, unit)
        collection = source.collection_for(dataset_id)
        logger.info("Dataset %s: ingesting %s to %s in %d chunks of one %s", dataset_id, start, stop, len(grid_chunks), unit)
        timeseries.ensure_collection(collection, source.granularity)

        delivered_any_chunk = False
        unreachable: SourceUnreachableError | None = None
        for chunk_index, chunk in enumerate(grid_chunks):
            status = chunk_status.get(dataset_id, chunk.cell_start)
            if status is not None and status.is_current(chunk.start, chunk.stop, datetime.now(UTC)):
                logger.info("Skipping chunk %s to %s of %s: its last fetch is current", chunk.start, chunk.stop, dataset_id)
            elif unreachable is not None:
                report.chunks_total += 1
                record_gap(report, dataset_id, chunk.start, chunk.stop, f"Not requested, the provider was unreachable: {unreachable}")
            else:
                fetch_start, fetch_stop = _fetch_range(chunk, status)
                report.chunks_total += 1
                try:
                    if _fetch_chunk(source, chunk_status, dataset_id, collection, chunk.cell_start, (fetch_start, fetch_stop)):
                        delivered_any_chunk = True
                except SourceUnreachableError as error:
                    logger.warning("Provider of %s unreachable, the remaining chunks of this run are not requested: %s", dataset_id, error)
                    record_gap(report, dataset_id, fetch_start, fetch_stop, f"{type(error).__name__}: {error}")
                    unreachable = error
                except CHUNK_ERRORS as error:
                    logger.warning("Chunk %s to %s of %s not ingested: %s", fetch_start, fetch_stop, dataset_id, error)
                    record_gap(report, dataset_id, fetch_start, fetch_stop, f"{type(error).__name__}: {error}")
            _report_chunk_progress(
                progress_callback, dataset_id, dataset_position=(dataset_index, len(time_ranges)), chunks_done=(chunk_index + 1, len(grid_chunks))
            )

        source.save_metadata(dataset_id, collection)
        if delivered_any_chunk:
            refresh_coverage(coverage_repository, dataset_id, collection)
    return report


def _fetch_chunk(
    source: ChunkSource, chunk_status: ChunkStatusRepository, dataset_id: str, collection: str, cell_start: datetime, fetch_range: TimeRange
) -> bool:
    """Ingests a range of one grid cell and records the status of the fetch.

    Args:
        source: The source-specific steps of the run.
        chunk_status: The repository storing the status of each fetched chunk.
        dataset_id: The dataset ID.
        collection: The collection holding the data documents.
        cell_start: The start of the grid cell.
        fetch_range: The start (inclusive) and end (exclusive) of the range to fetch, inside the cell.

    Returns:
        True if the provider delivered data for the range.

    Raises:
        DataRetrievalError: If the request failed.
        DataProcessingError: If the delivered data cannot be read.
        pymongo.errors.PyMongoError: If the documents or the status cannot be stored.
    """
    fetch_start, fetch_stop = fetch_range
    delivered = source.ingest_chunk(dataset_id, collection, fetch_start, fetch_stop)
    fetched_at = datetime.now(UTC)
    status = ChunkStatus(
        dataset_id=dataset_id,
        cell_start=cell_start,
        fetched_start=fetch_start,
        fetched_stop=fetch_stop,
        fetched_at=fetched_at,
        stable=source.is_stable(dataset_id, collection, fetch_start, fetch_stop, fetched_at),
    )
    chunk_status.save(status)
    return delivered


def _fetch_range(chunk: GridChunk, status: ChunkStatus | None) -> TimeRange:
    """Determines the range to fetch for a chunk: the requested part, widened to the range of the last fetch.

    The widening keeps the stored status covering everything fetched before, as the new status replaces it.

    Args:
        chunk: The requested part of the grid cell.
        status: The status of the last fetch of the cell; None if it was never fetched.

    Returns:
        The start (inclusive) and end (exclusive) of the range to fetch.
    """
    if status is None:
        return chunk.start, chunk.stop
    return min(chunk.start, status.fetched_start), max(chunk.stop, status.fetched_stop)


def generate_time_chunks(start_time: datetime, stop_time: datetime, unit: TimeBucket) -> list[GridChunk]:
    """Splits a time range into the parts of the calendar grid cells it touches.

    The cells are calendar hours, days, months or years in UTC, so chunks of later runs line up with earlier ones
    whatever range a run requests.

    Args:
        start_time: The start of the range (inclusive), timezone-aware.
        stop_time: The end of the range (exclusive), timezone-aware.
        unit: The calendar unit of the cells.

    Returns:
        The chunks in time order, the first and last cut to the range; empty for an empty or negative range.
    """
    chunks = []
    cell_start = calendar_cell_start(start_time, unit)
    while cell_start < stop_time:
        next_cell_start = next_calendar_cell(cell_start, unit)
        chunks.append(GridChunk(cell_start, max(cell_start, start_time), min(next_cell_start, stop_time)))
        cell_start = next_cell_start
    return chunks


def calendar_cell_start(time: datetime, unit: TimeBucket) -> datetime:
    """Returns the start of the calendar cell in UTC that contains a time.

    Args:
        time: The time, timezone-aware.
        unit: The calendar unit of the cells.

    Returns:
        The start of the hour, day, month or year of the time in UTC.
    """
    utc_time = time.astimezone(UTC)
    hour_start = utc_time.replace(minute=0, second=0, microsecond=0)
    match unit:
        case TimeBucket.HOUR:
            return hour_start
        case TimeBucket.DAY:
            return hour_start.replace(hour=0)
        case TimeBucket.MONTH:
            return hour_start.replace(day=1, hour=0)
        case TimeBucket.YEAR:
            return hour_start.replace(month=1, day=1, hour=0)


def next_calendar_cell(cell_start: datetime, unit: TimeBucket) -> datetime:
    """Returns the start of the calendar cell after the cell starting at a time.

    Args:
        cell_start: The start of a cell, as returned by calendar_cell_start.
        unit: The calendar unit of the cells.

    Returns:
        The start of the next hour, day, month or year.
    """
    match unit:
        case TimeBucket.HOUR:
            return cell_start + timedelta(hours=1)
        case TimeBucket.DAY:
            return cell_start + timedelta(days=1)
        case TimeBucket.MONTH:
            if cell_start.month == 12:
                return cell_start.replace(year=cell_start.year + 1, month=1)
            return cell_start.replace(month=cell_start.month + 1)
        case TimeBucket.YEAR:
            return cell_start.replace(year=cell_start.year + 1)


def chunk_unit_for_cadence(cadence: timedelta | None) -> TimeBucket:
    """Selects the calendar cell unit for data of a cadence, so that one request holds at most about 50,000 records.

    Args:
        cadence: The time between two records; None if unknown.

    Returns:
        A day below one minute or for an unknown cadence, a month below one hour, a year from one hour on.
    """
    if cadence is None or cadence < _DAY_CELL_MAX_CADENCE:
        return TimeBucket.DAY
    if cadence < _MONTH_CELL_MAX_CADENCE:
        return TimeBucket.MONTH
    return TimeBucket.YEAR


def stored_cadence(metadata_repository: MetadataRepository, dataset_id: str) -> timedelta | None:
    """Reads the cadence of a dataset from its stored metadata record.

    Args:
        metadata_repository: The repository storing the metadata records.
        dataset_id: The dataset ID.

    Returns:
        The cadence; None if the dataset has no record, the record no cadence or the cadence is no ISO 8601 duration.
    """
    record = metadata_repository.get(dataset_id)
    if record is None or record.time.cadence is None:
        return None
    return parse_iso_duration(record.time.cadence)


def settled(stop: datetime, fetched_at: datetime) -> bool:
    """Tells whether a fetched range ended at least SETTLE_DELAY before the fetch, so its data count as stable.

    Args:
        stop: The end of the fetched range (exclusive).
        fetched_at: When the fetch finished.

    Returns:
        True if the range ended SETTLE_DELAY or longer before the fetch.
    """
    return stop + SETTLE_DELAY <= fetched_at


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
        record_gap(report, dataset_id, start, stop, f"{type(error).__name__}: {error}")
        return None


def record_gap(report: IngestionReport, dataset_id: str, start: datetime, stop: datetime, reason: str) -> None:
    """Adds a chunk that was not ingested to the gaps of a report.

    Args:
        report: The report of the run, changed in place.
        dataset_id: The dataset ID.
        start: The start of the chunk (inclusive).
        stop: The end of the chunk (exclusive).
        reason: Why the chunk was not ingested.
    """
    report.gaps.append(ChunkGap(dataset_id=dataset_id, start=start, stop=stop, reason=reason))


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
