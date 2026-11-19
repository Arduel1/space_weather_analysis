import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from pymongo.errors import PyMongoError

from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.jobs.models import ChunkGap
from space_weather_analysis.metadata.minimal import build_minimal_metadata
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.timeseries.documents import TIME_FIELD
from space_weather_analysis.util.error_handling import DataProcessingError, DataRetrievalError
from space_weather_analysis.util.time_utils import ensure_aware
from space_weather_analysis.util.types import TimeRange

logger = logging.getLogger(__name__)

# Failures of one chunk that leave a gap but let the run go on: a failed request, unreadable data or a failed write.
CHUNK_ERRORS = (DataRetrievalError, DataProcessingError, PyMongoError)

# Failures of a metadata save, which leave the stored data documents valid.
METADATA_SAVE_ERRORS = (PyMongoError, ValidationError, ValueError)


def resolve_time_range(start_time: datetime | None, end_time: datetime | None, lookback: timedelta) -> TimeRange:
    """Completes the time range of a run: a missing end is now, a missing start lies the lookback before the end.

    Args:
        start_time: The start of the range (inclusive); None for the lookback before the end.
        end_time: The end of the range (exclusive); None for now.
        lookback: The length of a range without start.

    Returns:
        The timezone-aware start and end.
    """
    stop = ensure_aware(end_time) if end_time is not None else datetime.now(UTC)
    start = ensure_aware(start_time) if start_time is not None else stop - lookback
    return start, stop


def require_known_datasets(datasets: Sequence[str], known: Iterable[str], kind: str) -> None:
    """Rejects an empty dataset list and dataset IDs a pipeline does not know.

    Args:
        datasets: The requested dataset IDs.
        known: The dataset IDs the pipeline knows.
        kind: The kind of the datasets, named in the error message.

    Raises:
        ValueError: If no dataset is given or a dataset ID is unknown.
    """
    if not datasets:
        raise ValueError(f"The {kind} pipeline needs at least one dataset.")
    known_datasets = set(known)
    unknown_datasets = sorted(set(datasets) - known_datasets)
    if unknown_datasets:
        raise ValueError(f"Unknown {kind} datasets {unknown_datasets}; known are {sorted(known_datasets)}.")


def require_single_dataset(datasets: Sequence[str], dataset_id: str, pipeline: str) -> None:
    """Rejects a dataset list other than the one dataset a pipeline stores.

    Args:
        datasets: The requested dataset IDs.
        dataset_id: The one dataset of the pipeline.
        pipeline: The name of the pipeline, named in the error message.

    Raises:
        ValueError: If the list is not exactly the dataset of the pipeline.
    """
    if list(datasets) != [dataset_id]:
        raise ValueError(f"The {pipeline} pipeline stores only the dataset {dataset_id}, not {datasets}.")


def save_minimal_metadata(
    metadata_repository: MetadataRepository,
    dataset_id: str,
    collection: str,
    source_type: SourceType,
    fields: Iterable[str],
    server_url: str,
    *,
    refresh_after: timedelta | None = None,
) -> None:
    """Saves the metadata record of a dataset whose provider publishes none, built from its source and stored fields.

    A failing save is logged, as the stored data documents stay valid without metadata.

    Args:
        metadata_repository: The repository storing the metadata records.
        dataset_id: The dataset ID.
        collection: The collection holding the data documents.
        source_type: The source type the dataset is ingested from.
        fields: The fields of the stored documents.
        server_url: The location the data were read from.
        refresh_after: Age below which a stored record is kept instead of saved again; None saves in any case.
    """
    now = datetime.now(UTC)
    try:
        if refresh_after is not None:
            stored = metadata_repository.get(dataset_id)
            if stored is not None and now - stored.updated_at < refresh_after:
                return
        record = build_minimal_metadata(dataset_id, collection, source_type, fields, server_url, now)
        save_with_overlays(metadata_repository, [record])
    except METADATA_SAVE_ERRORS:
        logger.exception("Failed to save metadata of %s", dataset_id)


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
