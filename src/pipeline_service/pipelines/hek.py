from datetime import UTC, datetime, timedelta

import requests

from pipeline_service.chunk_status.repository import ChunkStatusRepository
from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.chunks import require_single_dataset, run_chunked, save_minimal_metadata
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.hek import HEK_PAGE_URL, REGION_ATTRIBUTES, fetch_regions
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.timeseries.documents import frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity, TimeBucket
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import ensure_aware

# The one dataset holding the daily NOAA sunspot regions of the HEK
HEK_REGIONS_DATASET = "hek_noaa_regions"
# Time after the end of a month from which the reports of its days are complete
REGIONS_SETTLED = timedelta(days=3)


class HekPipeline:
    """Stores the daily NOAA sunspot regions of the HEK month by month, skipping months whose entries are settled."""

    def __init__(
        self,
        *,
        timeseries: TimeSeriesRepository,
        metadata_repository: MetadataRepository,
        coverage_repository: CoverageRepository,
        chunk_status: ChunkStatusRepository,
    ) -> None:
        """Initializes the HEK pipeline.

        Args:
            timeseries: The repository storing the region documents.
            metadata_repository: The repository storing the metadata records.
            coverage_repository: The repository storing which time ranges are stored.
            chunk_status: The repository storing the status of each fetched month.
        """
        self.timeseries = timeseries
        self.metadata_repository = metadata_repository
        self.coverage_repository = coverage_repository
        self.chunk_status = chunk_status

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Stores the region entries of the time range month by month.

        Args:
            datasets: The dataset IDs; only HEK_REGIONS_DATASET is known.
            start_time: The start of the time range (inclusive).
            end_time: The end of the time range (exclusive); None ends now.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with the number of tried months and the months that failed.

        Raises:
            ValueError: If the start time is missing or a dataset other than HEK_REGIONS_DATASET is given.
        """
        require_single_dataset(datasets, HEK_REGIONS_DATASET, "HEK")
        if start_time is None:
            raise ValueError("The HEK pipeline needs a start time.")
        stop = ensure_aware(end_time) if end_time is not None else datetime.now(UTC)
        return run_chunked(
            HekRegionSource(self.timeseries, self.metadata_repository),
            {HEK_REGIONS_DATASET: (ensure_aware(start_time), stop)},
            timeseries=self.timeseries,
            coverage_repository=self.coverage_repository,
            chunk_status=self.chunk_status,
            progress_callback=progress_callback,
        )


class HekRegionSource:
    """The HEK steps of a chunked run over months of region entries."""

    # One entry per region and day
    granularity = Granularity.HOURS

    def __init__(self, timeseries: TimeSeriesRepository, metadata_repository: MetadataRepository) -> None:
        """Initializes the source for one run.

        Args:
            timeseries: The repository storing the region documents.
            metadata_repository: The repository storing the metadata records.
        """
        self.timeseries = timeseries
        self.metadata_repository = metadata_repository
        self.session = requests.Session()
        self._delivered = False

    def collection_for(self, dataset_id: str) -> str:
        """Names the collection of the dataset after its sanitized ID."""
        return sanitize_collection_name(dataset_id)

    def chunk_unit(self, dataset_id: str) -> TimeBucket:
        """Fetches the entries month by month, some hundred per month."""
        del dataset_id
        return TimeBucket.MONTH

    def is_stable(self, dataset_id: str, collection: str, start: datetime, stop: datetime, fetched_at: datetime) -> bool:
        """Counts a month as settled once REGIONS_SETTLED has passed after its end."""
        del dataset_id, collection, start
        return fetched_at >= stop + REGIONS_SETTLED

    def ingest_chunk(self, dataset_id: str, collection: str, start: datetime, stop: datetime) -> bool:
        """Fetches the entries of one month and replaces its stored documents; stored documents stay if the month has none.

        Raises:
            DataRetrievalError: If a request failed.
            DataProcessingError: If an answer cannot be read.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        regions = fetch_regions(self.session, start, stop)
        if regions.empty:
            return False
        self.timeseries.replace_range(collection, start, stop, frame_to_documents(regions, source_identifier(SourceType.HEK, dataset_id)))
        self._delivered = True
        return True

    def save_metadata(self, dataset_id: str, collection: str) -> None:
        """Saves the metadata record of the dataset once a month delivered entries; a failing save is logged."""
        if not self._delivered:
            return
        save_minimal_metadata(self.metadata_repository, dataset_id, collection, SourceType.HEK, REGION_ATTRIBUTES.values(), HEK_PAGE_URL)
