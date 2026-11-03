import logging
from datetime import UTC, datetime, timedelta

from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.chunks import CHUNK_ERRORS, record_gap, refresh_coverage
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.swpc import SWPC_PRODUCTS, SwpcClient
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.timeseries.documents import frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.error_handling import SourceUnreachableError
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)

# SWPC files hold one value per minute or per five minutes
SWPC_GRANULARITY = Granularity.MINUTES
# Range of a run without start time: the length of the shortest SWPC file
DEFAULT_LOOKBACK = timedelta(hours=6)


class SwpcPipeline:
    """Ingests the rolling JSON files of NOAA SWPC, one request per dataset and run.

    SWPC replaces its files every minute, so each run reads the file covering the requested range and writes only the
    documents that differ from the stored ones in the time span the file delivered.
    """

    def __init__(self, *, timeseries: TimeSeriesRepository, coverage_repository: CoverageRepository, client: SwpcClient) -> None:
        """Initializes the SWPC pipeline.

        Args:
            timeseries: The repository storing the data documents.
            coverage_repository: The repository storing which time ranges are stored.
            client: The client fetching the SWPC files.
        """
        self.timeseries = timeseries
        self.coverage_repository = coverage_repository
        self.client = client

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Ingests each dataset over the time range, as far back as the files of SWPC reach.

        Once SWPC sent no answer, the remaining datasets of the run are reported as gaps without a request.

        Args:
            datasets: The IDs of SWPC datasets, keys of SWPC_PRODUCTS.
            start_time: The start of the time range (inclusive); None starts DEFAULT_LOOKBACK before the end.
            end_time: The end of the time range (exclusive); None ends now.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with one chunk per dataset and the datasets that could not be ingested.

        Raises:
            ValueError: If no dataset is given or a dataset ID is no SWPC dataset.
        """
        if not datasets:
            raise ValueError("The SWPC pipeline needs at least one dataset.")
        unknown_datasets = sorted(set(datasets) - SWPC_PRODUCTS.keys())
        if unknown_datasets:
            raise ValueError(f"Unknown SWPC datasets {unknown_datasets}; known are {sorted(SWPC_PRODUCTS)}.")
        stop = ensure_aware(end_time) if end_time is not None else datetime.now(UTC)
        start = ensure_aware(start_time) if start_time is not None else stop - DEFAULT_LOOKBACK

        report = IngestionReport()
        unreachable: SourceUnreachableError | None = None
        for dataset_index, dataset_id in enumerate(datasets):
            report.chunks_total += 1
            if unreachable is not None:
                record_gap(report, dataset_id, start, stop, f"Not requested, the provider was unreachable: {unreachable}")
                continue
            collection = sanitize_collection_name(dataset_id)
            try:
                if self._ingest(dataset_id, collection, start, stop):
                    refresh_coverage(self.coverage_repository, dataset_id, collection)
            except SourceUnreachableError as error:
                logger.warning("SWPC unreachable, the remaining datasets of this run are not requested: %s", error)
                record_gap(report, dataset_id, start, stop, f"{type(error).__name__}: {error}")
                unreachable = error
            except CHUNK_ERRORS as error:
                logger.warning("SWPC dataset %s not ingested: %s", dataset_id, error)
                record_gap(report, dataset_id, start, stop, f"{type(error).__name__}: {error}")
            if progress_callback:
                progress_callback((dataset_index + 1) / len(datasets), f"Processed SWPC dataset {dataset_id}")
        return report

    def _ingest(self, dataset_id: str, collection: str, start: datetime, stop: datetime) -> bool:
        """Fetches the file of a dataset and writes the differences in the span from its first delivered time to the stop.

        The span starts at the first delivered time, not at the start, so stored documents older than the file stay.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).

        Returns:
            True if SWPC delivered data in the range.

        Raises:
            SourceUnreachableError: If SWPC sent no answer.
            DataRetrievalError: If the download failed.
            DataProcessingError: If the file cannot be read.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        result = self.client.fetch(dataset_id, start, stop)
        if result.data is None:
            logger.info("SWPC file of %s holds no value between %s and %s", dataset_id, start, stop)
            return False
        frame = result.data
        documents = frame_to_documents(frame, source_identifier(SourceType.SWPC, SWPC_PRODUCTS[dataset_id].provider_id))
        self.timeseries.ensure_collection(collection, SWPC_GRANULARITY)
        self.timeseries.replace_range(collection, frame.index.min().to_pydatetime(), stop, documents)
        return True
