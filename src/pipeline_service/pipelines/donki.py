from datetime import datetime, timedelta

import requests

from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.chunks import refresh_coverage, require_single_dataset, resolve_time_range, run_single_dataset, save_minimal_metadata
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.donki import DONKI_PAGE_URL, EVENT_FIELDS, fetch_events
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.timeseries.documents import frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.naming import sanitize_collection_name

# The one dataset holding the events of all DONKI event types
DONKI_DATASET = "donki_events"
# Events lie hours to days apart
DONKI_GRANULARITY = Granularity.HOURS
# Range of a run without start time
DEFAULT_LOOKBACK = timedelta(days=30)


class DonkiPipeline:
    """Stores the space weather events of DONKI with the links between them, one document per event.

    Each run fetches every event type for the whole range and replaces the stored events of the range, so that events
    forecasters add, revise or link later are taken over.
    """

    def __init__(self, *, timeseries: TimeSeriesRepository, coverage_repository: CoverageRepository, metadata_repository: MetadataRepository) -> None:
        """Initializes the DONKI pipeline.

        Args:
            timeseries: The repository storing the event documents.
            coverage_repository: The repository storing which time ranges are stored.
            metadata_repository: The repository storing the metadata records.
        """
        self.timeseries = timeseries
        self.coverage_repository = coverage_repository
        self.metadata_repository = metadata_repository
        self.session = requests.Session()

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Stores the events of the time range.

        Args:
            datasets: The dataset IDs; only DONKI_DATASET is known.
            start_time: The start of the time range (inclusive); None starts DEFAULT_LOOKBACK before the end.
            end_time: The end of the time range (exclusive); None ends now.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with one chunk for the range, a gap if it could not be ingested.

        Raises:
            ValueError: If a dataset other than DONKI_DATASET is given.
        """
        require_single_dataset(datasets, DONKI_DATASET, "DONKI")
        start, stop = resolve_time_range(start_time, end_time, DEFAULT_LOOKBACK)
        return run_single_dataset(
            DONKI_DATASET, (start, stop), lambda: self._store_events(start, stop), what="DONKI events", progress_callback=progress_callback
        )

    def _store_events(self, start: datetime, stop: datetime) -> None:
        """Fetches the events of the range and replaces the stored events of the range with them.

        Raises:
            DataRetrievalError: If a request failed.
            DataProcessingError: If an answer is no list of events.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        events = fetch_events(self.session, start, stop)
        collection = sanitize_collection_name(DONKI_DATASET)
        self.timeseries.ensure_collection(collection, DONKI_GRANULARITY)
        # DONKI answers by whole days, so the events outside the exact range are dropped before the range is replaced
        in_range = events[(events.index >= start) & (events.index < stop)]
        self.timeseries.replace_range(collection, start, stop, frame_to_documents(in_range, source_identifier(SourceType.DONKI, DONKI_DATASET)))
        refresh_coverage(self.coverage_repository, DONKI_DATASET, collection)
        save_minimal_metadata(self.metadata_repository, DONKI_DATASET, collection, SourceType.DONKI, EVENT_FIELDS, DONKI_PAGE_URL)
