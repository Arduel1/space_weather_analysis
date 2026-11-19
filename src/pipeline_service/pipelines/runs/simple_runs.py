import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.report import IngestionReport
from pipeline_service.pipelines.runs.run_steps import CHUNK_ERRORS, record_gap
from space_weather_analysis.timeseries.documents import TimeSeriesDocument
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.error_handling import SourceUnreachableError
from space_weather_analysis.util.types import TimeRange

logger = logging.getLogger(__name__)


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


def run_single_dataset(
    dataset_id: str,
    gap_range: TimeRange,
    ingest: Callable[[], str | None],
    *,
    what: str,
    errors: tuple[type[Exception], ...] = CHUNK_ERRORS,
    progress_callback: ProgressCallback | None = None,
) -> IngestionReport:
    """Ingests the one dataset of a pipeline as one chunk, recording a gap if it fails or stores nothing.

    Args:
        dataset_id: The dataset ID.
        gap_range: The start and end recorded with a gap.
        ingest: Ingests the dataset; returns the reason of a gap if it stored nothing, else None.
        what: What the dataset holds, named in the log and the progress message.
        errors: The failures that leave a gap instead of ending the run.
        progress_callback: Receives the progress of the run; None to report nothing.

    Returns:
        The report with one chunk, a gap if nothing was stored.
    """
    report = IngestionReport(chunks_total=1)
    start, stop = gap_range
    try:
        gap_reason = ingest()
    except errors as error:
        # The log message starts a sentence with the subject
        logger.warning("%s not stored: %s", what[:1].upper() + what[1:], error)
        gap_reason = f"{type(error).__name__}: {error}"
    if gap_reason is not None:
        record_gap(report, dataset_id, start, stop, gap_reason)
    if progress_callback:
        progress_callback(1.0, f"Processed the {what}")
    return report


def replace_all_documents(
    timeseries: TimeSeriesRepository, collection: str, granularity: Granularity, documents: Sequence[TimeSeriesDocument]
) -> None:
    """Makes a collection hold exactly the given documents, creating it if needed.

    Args:
        timeseries: The repository storing the documents.
        collection: The collection.
        granularity: The time between the documents, set when the collection is created.
        documents: The documents; empty removes all.

    Raises:
        pymongo.errors.PyMongoError: If the documents cannot be stored.
    """
    timeseries.ensure_collection(collection, granularity)
    timeseries.replace_range(collection, datetime.min.replace(tzinfo=UTC), datetime.max.replace(tzinfo=UTC), documents)


def run_per_dataset(
    datasets: Sequence[str],
    time_range: TimeRange,
    ingest: Callable[[str], object],
    *,
    provider: str,
    dataset_kind: str,
    errors: tuple[type[Exception], ...] = CHUNK_ERRORS,
    progress_callback: ProgressCallback | None = None,
) -> IngestionReport:
    """Ingests each dataset over the whole time range in one chunk, recording a failed dataset as gap.

    Once the provider sent no answer, the remaining datasets of the run become gaps without a request.

    Args:
        datasets: The dataset IDs in the order of the run.
        time_range: The start (inclusive) and end (exclusive) of the range, recorded with each gap.
        ingest: Ingests one dataset, given its ID.
        provider: The name of the provider, named in the log when it is unreachable.
        dataset_kind: The kind of the datasets, named in the log and the progress messages.
        errors: The failures of one dataset that leave a gap and let the run go on.
        progress_callback: Receives the progress of the run; None to report nothing.

    Returns:
        The report with one chunk per dataset and the datasets that could not be ingested.
    """
    start, stop = time_range
    # The log message starts a sentence with the kind
    capitalized_kind = dataset_kind[:1].upper() + dataset_kind[1:]
    report = IngestionReport()
    unreachable: SourceUnreachableError | None = None
    for dataset_index, dataset_id in enumerate(datasets):
        report.chunks_total += 1
        if unreachable is not None:
            record_gap(report, dataset_id, start, stop, f"Not requested, the provider was unreachable: {unreachable}")
            continue
        try:
            ingest(dataset_id)
        except SourceUnreachableError as error:
            logger.warning("%s unreachable, the remaining datasets of this run are not requested: %s", provider, error)
            record_gap(report, dataset_id, start, stop, f"{type(error).__name__}: {error}")
            unreachable = error
        except errors as error:
            logger.warning("%s dataset %s not ingested: %s", capitalized_kind, dataset_id, error)
            record_gap(report, dataset_id, start, stop, f"{type(error).__name__}: {error}")
        if progress_callback:
            progress_callback((dataset_index + 1) / len(datasets), f"Processed {dataset_kind} dataset {dataset_id}")
    return report
