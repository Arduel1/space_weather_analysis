import logging
from datetime import UTC, datetime, timedelta

from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.chunks import METADATA_SAVE_ERRORS, refresh_coverage, require_known_datasets, resolve_time_range, run_per_dataset
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.swpc import SwpcClient
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.metadata.minimal import build_minimal_metadata
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.timeseries.documents import frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.naming import sanitize_collection_name

logger = logging.getLogger(__name__)

# SWPC files hold one value per minute or per five minutes
SWPC_GRANULARITY = Granularity.MINUTES
# Range of a run without start time: the length of the shortest SWPC file
DEFAULT_LOOKBACK = timedelta(hours=6)
# Age after which the metadata record of a dataset is saved again
METADATA_REFRESH = timedelta(hours=1)
# Time before the newest stored document from which a run fetches again, so that late corrections of SWPC are taken up
REFETCH_OVERLAP = timedelta(hours=1)
# Time the oldest stored document may lie after the start for the stored data to count as reaching back to it; the
# oldest row of a rolling file lies a moment after the start of a lookback as long as the file
STORED_START_TOLERANCE = timedelta(hours=1)
# Time after the last value of a model run up to which the stored values are replaced, as the end of a range is exclusive
RUN_END_MARGIN = timedelta(seconds=1)


class SwpcPipeline:
    """Ingests the rolling JSON files of NOAA SWPC, one request per dataset and run.

    SWPC replaces its files every minute, so each run reads the file covering the requested range and writes only the
    documents that differ from the stored ones in the time span the file delivered. Once the stored documents reach back
    to the start, a run starts REFETCH_OVERLAP before the newest stored document, so after a long first range the runs
    fetch only the short files.
    """

    def __init__(
        self,
        *,
        timeseries: TimeSeriesRepository,
        coverage_repository: CoverageRepository,
        metadata_repository: MetadataRepository,
        client: SwpcClient,
    ) -> None:
        """Initializes the SWPC pipeline.

        Args:
            timeseries: The repository storing the data documents.
            coverage_repository: The repository storing which time ranges are stored.
            metadata_repository: The repository storing the metadata records.
            client: The client fetching the SWPC files.
        """
        self.timeseries = timeseries
        self.coverage_repository = coverage_repository
        self.metadata_repository = metadata_repository
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
            datasets: The IDs of SWPC datasets, keys of the products of the client.
            start_time: The start of the time range (inclusive); None starts DEFAULT_LOOKBACK before the end.
            end_time: The end of the time range (exclusive); None ends now.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with one chunk per dataset and the datasets that could not be ingested.

        Raises:
            ValueError: If no dataset is given or a dataset ID is no SWPC dataset.
        """
        require_known_datasets(datasets, self.client.products, "SWPC")
        start, stop = resolve_time_range(start_time, end_time, DEFAULT_LOOKBACK)

        return run_per_dataset(
            datasets,
            (start, stop),
            lambda dataset_id: self._ingest_and_refresh(dataset_id, start, stop),
            provider="SWPC",
            dataset_kind="SWPC",
            progress_callback=progress_callback,
        )

    def _ingest_and_refresh(self, dataset_id: str, start: datetime, stop: datetime) -> None:
        """Ingests a dataset and refreshes its coverage if SWPC delivered data.

        Raises:
            SourceUnreachableError: If SWPC sent no answer.
            DataRetrievalError: If the download failed.
            DataProcessingError: If the file cannot be read.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        collection = sanitize_collection_name(dataset_id)
        if self._ingest(dataset_id, collection, start, stop):
            refresh_coverage(self.coverage_repository, dataset_id, collection)

    def _ingest(self, dataset_id: str, collection: str, start: datetime, stop: datetime) -> bool:
        """Fetches the file of a dataset from the start or shortly before its newest stored document, and writes the differences.

        See _fetch_start for where the fetch starts.

        The written span starts at the first delivered time, not at the start, so stored documents older than the file stay.
        A model run reaching ahead is fetched from the start and replaces the stored values up to its last time.

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
        product = self.client.products[dataset_id]
        fetch_start = start if product.reaches_ahead else self._fetch_start(dataset_id, collection, start)
        result = self.client.fetch(dataset_id, fetch_start, stop)
        if result.data is None:
            logger.info("SWPC file of %s holds no value between %s and %s", dataset_id, fetch_start, stop)
            return False
        frame = result.data
        documents = frame_to_documents(frame, source_identifier(SourceType.SWPC, product.provider_id))
        self.timeseries.ensure_collection(collection, SWPC_GRANULARITY)
        # The values of a model run after now replace those of the previous run as well
        written_stop = max(stop, frame.index.max().to_pydatetime() + RUN_END_MARGIN) if product.reaches_ahead else stop
        self.timeseries.replace_range(collection, frame.index.min().to_pydatetime(), written_stop, documents)
        self._save_metadata(dataset_id, collection, [str(column) for column in frame.columns], result.source_url or self.client.base_url)
        return True

    def _save_metadata(self, dataset_id: str, collection: str, fields: list[str], server_url: str) -> None:
        """Saves the metadata record of a dataset unless the stored one describes the same fields and is younger than METADATA_REFRESH.

        SWPC publishes no metadata, so the record holds the source and the fields, and the curated metadata laid over it
        on saving adds the descriptions. Runs come every minute, so the record is written only when it is missing, the
        delivered fields changed or METADATA_REFRESH has passed, which takes up changed curated metadata.
        A failing save is logged, as the stored data documents stay valid without metadata.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
            fields: The fields of the delivered documents.
            server_url: The URL of the fetched file.
        """
        now = datetime.now(UTC)
        try:
            stored = self.metadata_repository.get(dataset_id)
            if stored is not None and {variable.name for variable in stored.variables} == set(fields) and now - stored.updated_at < METADATA_REFRESH:
                return
            record = build_minimal_metadata(dataset_id, collection, SourceType.SWPC, fields, server_url, now)
            save_with_overlays(self.metadata_repository, [record])
        except METADATA_SAVE_ERRORS:
            logger.exception("Failed to save metadata of %s", dataset_id)
            return
        logger.info("Saved metadata of %s", dataset_id)

    def _fetch_start(self, dataset_id: str, collection: str, start: datetime) -> datetime:
        """Determines where a run fetches from: shortly before the newest stored document if the stored ones reach back to the start.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
            start: The start of the requested range.

        Returns:
            REFETCH_OVERLAP before the newest stored document, or the start if nothing is stored or the oldest stored
            document lies more than STORED_START_TOLERANCE after the start.

        Raises:
            pymongo.errors.PyMongoError: If the coverage or the newest document cannot be read.
        """
        coverage = self.coverage_repository.get(dataset_id)
        if coverage is None or coverage.first_timestamp is None or coverage.first_timestamp > start + STORED_START_TOLERANCE:
            return start
        newest = self.timeseries.read_latest(collection, ())
        return max(start, newest[0] - REFETCH_OVERLAP) if newest is not None else start
