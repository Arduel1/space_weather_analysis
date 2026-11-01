import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from cdasws import CdasWs

from pipeline_service.parsing.cdaweb_xarray import VariablePlan, dataset_to_frame, plan_master_variables
from pipeline_service.parsing.istp import clean_istp_columns
from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.chunks import METADATA_SAVE_ERRORS, run_chunked
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.clients.cdaweb import CdawebClient
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.metadata.cdaweb_file import DeliveredMetadata, build_cdaweb_file_metadata, column_data_type
from space_weather_analysis.metadata.models import MetadataSourceKind
from space_weather_analysis.metadata.overlays.cdaweb import cdas_dataset_id
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.timeseries.documents import frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)

# Time span of one request and of one check for already stored documents.
DEFAULT_CHUNK_DURATION = timedelta(days=7)


class CdawebPipeline:
    """Ingests CDAWeb datasets chunk by chunk from the CDAS web services.

    CDAWeb data follow the ISTP Metadata Guidelines (International Solar-Terrestrial
    Physics, https://istp-metadata.readthedocs.io/), which define the attributes of each
    variable, such as fill value (FILLVAL), valid range (VALIDMIN/VALIDMAX), unit (UNITS)
    and description (CATDESC). The pipeline uses them to clean the data and to describe it.
    """

    def __init__(
        self,
        *,
        timeseries: TimeSeriesRepository,
        catalog_repository: CatalogRepository,
        metadata_repository: MetadataRepository,
        coverage_repository: CoverageRepository,
        cdas: CdasWs,
        chunk_duration: timedelta = DEFAULT_CHUNK_DURATION,
    ) -> None:
        """Initializes the pipeline.

        Args:
            timeseries: The repository storing the data documents.
            catalog_repository: The repository of the master catalog, which names the observatory group of each dataset.
            metadata_repository: The repository storing the metadata records and holding the stored master files.
            coverage_repository: The repository storing which time ranges are stored.
            cdas: The CDAS web service client the data are requested with.
            chunk_duration: The time span of one request.
        """
        self.timeseries = timeseries
        self.catalog_repository = catalog_repository
        self.metadata_repository = metadata_repository
        self.coverage_repository = coverage_repository
        self.chunk_duration = chunk_duration
        self._variable_plans: dict[str, VariablePlan] = {}
        self.client = CdawebClient(cdas=cdas, variables_for=self._requested_variables)

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Ingests each dataset in chunks of the chunk duration, skipping chunks that already hold documents.

        The metadata record of a dataset is built once after its chunks, from what all of them delivered.

        Args:
            datasets: The IDs of the datasets to ingest.
            start_time: The start of the time range (inclusive).
            end_time: The end of the time range (exclusive).
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with the number of tried chunks and the chunks that failed.

        Raises:
            ValueError: If start or end time is missing, no dataset is given or a dataset has no CDAWEB source in the master catalog.
        """
        if not start_time or not end_time:
            raise ValueError("The CDAWeb pipeline needs a start and an end time.")
        if not datasets:
            raise ValueError("The CDAWeb pipeline needs at least one dataset.")
        group_names = {dataset_id: self._find_cdaweb_group(dataset_id) for dataset_id in datasets}
        self._variable_plans = self._plan_variables(datasets)

        time_range = (ensure_aware(start_time), ensure_aware(end_time))
        return run_chunked(
            CdawebChunkSource(self.client, self.timeseries, self.metadata_repository, group_names, self._variable_plans),
            dict.fromkeys(datasets, time_range),
            chunk_duration=self.chunk_duration,
            timeseries=self.timeseries,
            coverage_repository=self.coverage_repository,
            progress_callback=progress_callback,
        )

    def _find_cdaweb_group(self, dataset_id: str) -> str:
        """Reads the observatory group of the first CDAWEB source of a dataset from the master catalog.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The group name, the source_id of the source.

        Raises:
            ValueError: If the master catalog lists no CDAWEB source with a group for the dataset.
        """
        for source in self.catalog_repository.get_sources(dataset_id, SourceType.CDAWEB):
            group_name = source.get("source_id")
            if group_name:
                return group_name
        raise ValueError(f"The master catalog lists no {SourceType.CDAWEB} source for dataset '{dataset_id}'.")

    def _plan_variables(self, datasets: Sequence[str]) -> dict[str, VariablePlan]:
        """Selects the variables to request for each dataset from its stored CDAWeb master file.

        Args:
            datasets: The dataset IDs.

        Returns:
            The plan of each dataset with a stored master file; datasets without one request the variables CDAS lists.
        """
        cdas_ids = sorted({cdas_dataset_id(dataset_id) for dataset_id in datasets})
        raw_masters = self.metadata_repository.get_raw_many(cdas_ids, MetadataSourceKind.ISTP)
        plans: dict[str, VariablePlan] = {}
        for dataset_id in datasets:
            raw_master = raw_masters.get(cdas_dataset_id(dataset_id))
            if raw_master is not None:
                plans[dataset_id] = plan_master_variables(raw_master.payload)
        return plans

    def _requested_variables(self, dataset_id: str) -> Sequence[str] | None:
        """Names the variables the client requests for a dataset.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The variables planned from the master file, or None if the dataset has no plan.
        """
        plan = self._variable_plans.get(dataset_id)
        return plan.requested if plan is not None else None


class CdawebChunkSource:
    """The CDAWeb steps of a chunked run, collecting the metadata the chunks of each dataset delivered."""

    # CDAWeb datasets have cadences from seconds to minutes
    granularity = Granularity.SECONDS

    def __init__(
        self,
        client: CdawebClient,
        timeseries: TimeSeriesRepository,
        metadata_repository: MetadataRepository,
        group_names: Mapping[str, str],
        variable_plans: Mapping[str, VariablePlan],
    ) -> None:
        """Initializes the source for one run.

        Args:
            client: The client requesting the data from CDAS.
            timeseries: The repository storing the data documents.
            metadata_repository: The repository storing the metadata records.
            group_names: The observatory group of each dataset, part of the source identifier of its documents.
            variable_plans: The variables planned from the stored master file of each dataset that has one.
        """
        self.client = client
        self.timeseries = timeseries
        self.metadata_repository = metadata_repository
        self.group_names = group_names
        self.variable_plans = variable_plans
        self._delivered: dict[str, DeliveredMetadata] = {}

    def collection_for(self, dataset_id: str) -> str:
        """Names the collection of a dataset after its sanitized ID.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The collection name.
        """
        return sanitize_collection_name(dataset_id)

    def should_skip(self, dataset_id: str, collection: str, start: datetime, stop: datetime) -> bool:
        """Skips a chunk that holds at least one stored document.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
            start: The start of the chunk (inclusive).
            stop: The end of the chunk (exclusive).

        Returns:
            True if the chunk holds a stored document.
        """
        del dataset_id
        return self.timeseries.has_documents(collection, start, stop)

    def ingest_chunk(self, dataset_id: str, collection: str, start: datetime, stop: datetime) -> bool:
        """Requests, cleans and stores one chunk of a dataset and adds what it delivered to the metadata of the run.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
            start: The start of the chunk (inclusive).
            stop: The end of the chunk (exclusive).

        Returns:
            True if CDAS delivered data for the chunk.

        Raises:
            DataRetrievalError: If the request failed.
            DataProcessingError: If CDAS delivered no readable data.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        result = self.client.fetch(dataset_id, start, stop)
        if result.data is None:
            logger.info("No data of %s for %s to %s", dataset_id, start, stop)
            return False

        dataset_frame = dataset_to_frame(result.data, self.client.variables(dataset_id), start, stop)
        cleaned_frame = clean_istp_columns(dataset_frame.frame, dataset_frame.attributes)
        source = source_identifier(SourceType.CDAWEB, self.group_names[dataset_id])
        self.timeseries.insert(collection, frame_to_documents(cleaned_frame, source))

        delivered = self._delivered.setdefault(dataset_id, DeliveredMetadata())
        plan = self.variable_plans.get(dataset_id)
        for skipped in [*(plan.not_stored if plan else ()), *dataset_frame.not_stored]:
            delivered.not_stored.setdefault(skipped.name, skipped)
        delivered.variable_attributes.update(dataset_frame.attributes)
        for column_name in cleaned_frame.columns:
            delivered.column_types[str(column_name)] = column_data_type(cleaned_frame[column_name])
        return True

    def save_metadata(self, dataset_id: str, collection: str) -> None:
        """Saves the metadata record built from the ISTP attributes delivered with the data and the attributes themselves.

        The attributes are of kind "file", as the master file of the dataset, laid over the record as kind "istp",
        overrides them. A failing build or save is logged, as the stored data documents stay valid without metadata.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
        """
        delivered = self._delivered.get(dataset_id)
        if delivered is None:
            return
        if delivered.not_stored:
            logger.warning("Dataset %s: variables not stored: %s", dataset_id, sorted(delivered.not_stored))
        if not delivered.variable_attributes:
            return

        retrieved_at = datetime.now(UTC)
        try:
            record = build_cdaweb_file_metadata(dataset_id, collection, delivered, retrieved_at)
            self.metadata_repository.save_raw(dataset_id, MetadataSourceKind.FILE, delivered.variable_attributes, None, retrieved_at)
            save_with_overlays(self.metadata_repository, [record])
        except METADATA_SAVE_ERRORS:
            logger.exception("Failed to save metadata of %s", dataset_id)
            return
        logger.info("Saved metadata of %s", dataset_id)
