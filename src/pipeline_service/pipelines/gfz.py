import logging
import math
from datetime import UTC, datetime, timedelta

import pandas as pd

from pipeline_service.chunk_status.repository import ChunkStatusRepository
from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.chunks import METADATA_SAVE_ERRORS, chunk_unit_for_cadence, run_chunked
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.gfz import GFZ_INDICES, GfzClient, GfzStatus
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.metadata.gfz import build_gfz_metadata
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.timeseries.documents import frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity, TimeBucket
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)


class GfzPipeline:
    """Ingests the geomagnetic indices of GFZ Potsdam chunk by chunk into MongoDB time series collections.

    A chunk counts as stable once it holds a value for every interval of the index and none of them is preliminary.
    """

    def __init__(
        self,
        *,
        timeseries: TimeSeriesRepository,
        metadata_repository: MetadataRepository,
        coverage_repository: CoverageRepository,
        chunk_status: ChunkStatusRepository,
        client: GfzClient,
        chunk_unit: TimeBucket | None = None,
    ) -> None:
        """Initializes the GFZ pipeline.

        Args:
            timeseries: The repository storing the data documents.
            metadata_repository: The repository storing the metadata records.
            coverage_repository: The repository storing which time ranges are stored.
            chunk_status: The repository storing the status of each fetched chunk.
            client: The client fetching the index values.
            chunk_unit: The calendar unit of the chunks; None selects it per index from its cadence.
        """
        self.timeseries = timeseries
        self.metadata_repository = metadata_repository
        self.coverage_repository = coverage_repository
        self.chunk_status = chunk_status
        self.client = client
        self.chunk_unit = chunk_unit

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Ingests each index in chunks of a calendar grid, skipping chunks whose last fetch is current.

        Args:
            datasets: The GFZ index names.
            start_time: Start of the data range (inclusive); a naive value is read as UTC.
            end_time: End of the data range (exclusive); a naive value is read as UTC.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with the number of tried chunks and the chunks that failed.

        Raises:
            ValueError: If start or end time is missing, no index is given or GFZ offers an index of no such name.
        """
        if not start_time or not end_time:
            raise ValueError("The GFZ pipeline needs a start and an end time.")
        if not datasets:
            raise ValueError("The GFZ pipeline needs at least one index.")
        unknown_indices = sorted(set(datasets) - self.client.ALLOWED_INDICES)
        if unknown_indices:
            raise ValueError(f"Unknown GFZ indices {unknown_indices}; allowed are {sorted(self.client.ALLOWED_INDICES)}.")

        time_range = (ensure_aware(start_time), ensure_aware(end_time))
        return run_chunked(
            GfzChunkSource(self.client, self.timeseries, self.metadata_repository, self.chunk_unit),
            dict.fromkeys(datasets, time_range),
            timeseries=self.timeseries,
            coverage_repository=self.coverage_repository,
            chunk_status=self.chunk_status,
            progress_callback=progress_callback,
        )


class GfzChunkSource:
    """The GFZ steps of a chunked run, keeping the latest response of each index for its metadata."""

    # The Kp index has one value every 3 hours
    granularity = Granularity.HOURS

    def __init__(self, client: GfzClient, timeseries: TimeSeriesRepository, metadata_repository: MetadataRepository, unit: TimeBucket | None) -> None:
        """Initializes the source for one run.

        Args:
            client: The client fetching the index values.
            timeseries: The repository storing the data documents.
            metadata_repository: The repository storing the metadata records.
            unit: The calendar unit of the chunks; None selects it per index from its cadence.
        """
        self.client = client
        self.timeseries = timeseries
        self.metadata_repository = metadata_repository
        self.unit = unit
        self._latest_response: dict[str, pd.DataFrame] = {}

    def collection_for(self, dataset_id: str) -> str:
        """Names the collection of an index after its sanitized name.

        Args:
            dataset_id: The GFZ index name.

        Returns:
            The collection name.
        """
        return sanitize_collection_name(dataset_id)

    def chunk_unit(self, dataset_id: str) -> TimeBucket:
        """Names the calendar cell unit of an index, the given unit or the one fitting its cadence.

        Args:
            dataset_id: The GFZ index name.

        Returns:
            The cell unit.
        """
        return self.unit or chunk_unit_for_cadence(GFZ_INDICES[dataset_id].cadence)

    def is_stable(self, dataset_id: str, collection: str, start: datetime, stop: datetime, fetched_at: datetime) -> bool:
        """Counts a fetched range as stable once its stored documents hold one value per interval and no preliminary value.

        Args:
            dataset_id: The GFZ index name.
            collection: The collection holding the index.
            start: Start of the fetched range (inclusive).
            stop: End of the fetched range (exclusive).
            fetched_at: When the fetch finished.

        Returns:
            True if the stored range is complete and definitive.
        """
        del fetched_at
        if self.timeseries.count(collection, start, stop) != count_grid_times(start, stop, GFZ_INDICES[dataset_id].cadence):
            return False
        return self.timeseries.count(collection, start, stop, data_values={"status": GfzStatus.PRELIMINARY}) == 0

    def ingest_chunk(self, dataset_id: str, collection: str, start: datetime, stop: datetime) -> bool:
        """Fetches one chunk and replaces its stored documents; stored documents stay if the chunk holds no value.

        Args:
            dataset_id: The GFZ index name.
            collection: The collection holding the index.
            start: Start of the chunk (inclusive).
            stop: End of the chunk (exclusive).

        Returns:
            True if GFZ has a value inside the chunk.

        Raises:
            DataRetrievalError: If the request failed.
            DataProcessingError: If the response is no index data.
            pymongo.errors.PyMongoError: If the documents cannot be replaced.
        """
        result = self.client.fetch(dataset_id, start, stop)
        if result.data is None:
            logger.info("No value of %s inside chunk %s to %s; stored documents are kept", dataset_id, start, stop)
            return False

        documents = frame_to_documents(result.data, source_identifier(SourceType.GFZ_POTSDAM, dataset_id))
        self.timeseries.replace_range(collection, start, stop, documents)
        self._latest_response[dataset_id] = result.data
        return True

    def save_metadata(self, dataset_id: str, collection: str) -> None:
        """Saves the metadata record of an index, with license and provider from the meta object of its latest response.

        A failing build or save is logged, as the stored data documents stay valid without metadata.

        Args:
            dataset_id: The GFZ index name.
            collection: The collection holding the index.
        """
        response = self._latest_response.get(dataset_id)
        if response is None:
            return
        retrieved_at = datetime.now(UTC)
        try:
            record = build_gfz_metadata(
                dataset_id, collection, response.attrs.get("meta", {}), "status" in response.columns, self.client.base_url, retrieved_at
            )
            save_with_overlays(self.metadata_repository, [record])
        except METADATA_SAVE_ERRORS:
            logger.exception("Failed to save metadata of %s", dataset_id)


def count_grid_times(start: datetime, stop: datetime, cadence: timedelta) -> int:
    """Counts the interval start times in [start, stop) on a grid anchored at 00:00 UTC.

    Args:
        start: Start of the range (inclusive), timezone-aware.
        stop: End of the range (exclusive), timezone-aware.
        cadence: Grid spacing; it must divide one day.

    Returns:
        The number of grid times inside the range.
    """
    day_start = start.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    # The first grid time at or after start
    first_grid_time = day_start + math.ceil((start - day_start) / cadence) * cadence
    if first_grid_time >= stop:
        return 0
    # Grid times first_grid_time, first_grid_time + cadence, ... before stop
    return math.ceil((stop - first_grid_time) / cadence)
