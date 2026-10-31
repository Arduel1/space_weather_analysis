import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import pandas as pd
from cdasws import CdasWs
from pymongo.database import Database

from pipeline_service.database.mongo_loader import MongoLoader
from pipeline_service.parsing.cdaweb_xarray import VariablePlan, dataset_to_frame, plan_master_variables
from pipeline_service.parsing.istp import (
    clean_istp_columns,
    istp_scalar_attribute,
    istp_text_attribute,
    map_columns_to_istp_variables,
    time_stamp_location,
)
from pipeline_service.pipelines.base import METADATA_SAVE_ERRORS, BasePipeline, ProgressCallback
from pipeline_service.pipelines.report import IngestionReport
from pipeline_service.util.documents import TIME_FIELD, dataframe_to_documents
from pipeline_service.util.time import generate_time_chunks
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.clients.cdaweb import CdawebClient
from space_weather_analysis.coverage.repository import CoverageRepository, MongoCoverageRepository
from space_weather_analysis.metadata.models import (
    DatasetMetadata,
    DataType,
    MetadataSource,
    MetadataSourceKind,
    SkippedParameter,
    SourceInfo,
    TimeInfo,
    VariableMetadata,
)
from space_weather_analysis.metadata.normalize import normalize_units
from space_weather_analysis.metadata.overlays.cdaweb import cdas_dataset_id
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)

# Time span of one request and of one check for already stored documents.
DEFAULT_CHUNK_DURATION = timedelta(days=7)


@dataclass
class DeliveredMetadata:
    """The metadata delivered with the chunks of one dataset in a run, collected for one record per dataset.

    Attributes:
        variable_attributes: The ISTP attributes of each delivered variable and time axis, keyed by name.
        column_types: The value type of each stored column.
        not_stored: The variables left out of the documents, keyed by name.
    """

    variable_attributes: dict[str, Any] = field(default_factory=dict)
    column_types: dict[str, DataType | None] = field(default_factory=dict)
    not_stored: dict[str, SkippedParameter] = field(default_factory=dict)


class CdawebPipeline(MongoLoader, BasePipeline):
    """Ingests CDAWeb datasets chunk by chunk from the CDAS web services.

    CDAWeb data follow the ISTP Metadata Guidelines (International Solar-Terrestrial
    Physics, https://istp-metadata.readthedocs.io/), which define the attributes of each
    variable, such as fill value (FILLVAL), valid range (VALIDMIN/VALIDMAX), unit (UNITS)
    and description (CATDESC). The pipeline uses them to clean the data and to describe it.
    """

    def __init__(self, db: Database, cdas: CdasWs | None = None, chunk_duration: timedelta = DEFAULT_CHUNK_DURATION) -> None:
        """Initializes the pipeline.

        Args:
            db: The MongoDB database instance.
            cdas: The CDAS web service client the data are requested with; a new one if None.
            chunk_duration: The time span of one request.
        """
        super().__init__(db)
        self.chunk_duration = chunk_duration
        self.metadata_repository: MetadataRepository = MongoMetadataRepository(db)
        self.catalog_repository: CatalogRepository = MongoCatalogRepository(db)
        self.coverage_repository: CoverageRepository = MongoCoverageRepository(db)
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

        time_chunks = generate_time_chunks(ensure_aware(start_time), ensure_aware(end_time), self.chunk_duration)
        report = IngestionReport()
        for dataset_index, dataset_id in enumerate(datasets):
            collection_name = sanitize_collection_name(dataset_id)
            self._ensure_timeseries_collection(collection_name)
            logger.info("Processing dataset %s (group %s) in %d chunks...", dataset_id, group_names[dataset_id], len(time_chunks))
            delivered = DeliveredMetadata()
            processed_any_chunk = False

            for chunk_index, (chunk_start, chunk_stop) in enumerate(time_chunks):
                self._report_chunk_progress(
                    progress_callback, dataset_id, dataset_position=(dataset_index, len(datasets)), chunk_position=(chunk_index, len(time_chunks))
                )
                if self.db[collection_name].find_one({TIME_FIELD: {"$gte": chunk_start, "$lt": chunk_stop}}, {"_id": 1}):
                    logger.info("Skipping chunk %s to %s of %s: documents already stored.", chunk_start, chunk_stop, dataset_id)
                    continue

                self._ingest_chunk(
                    report,
                    dataset_id,
                    chunk_start,
                    chunk_stop,
                    partial(self._process_chunk, dataset_id, group_names[dataset_id], chunk_start, chunk_stop, delivered),
                )
                processed_any_chunk = True

            if delivered.not_stored:
                logger.warning("Dataset %s: variables not stored: %s", dataset_id, sorted(delivered.not_stored))
            if delivered.variable_attributes:
                self._save_pipeline_metadata(dataset_id, delivered)
            if processed_any_chunk:
                self._refresh_coverage(self.coverage_repository, dataset_id, collection_name)

        return report

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

    def _process_chunk(self, dataset_id: str, group_name: str, start_time: datetime, end_time: datetime, delivered: DeliveredMetadata) -> None:
        """Requests, cleans and stores one chunk of a dataset and adds what it delivered to the metadata of the run.

        Args:
            dataset_id: The dataset ID.
            group_name: The observatory group of the dataset, part of the source identifier of the documents.
            start_time: The start of the chunk (inclusive).
            end_time: The end of the chunk (exclusive).
            delivered: The metadata delivered by the chunks of the run so far, changed in place.

        Raises:
            DataRetrievalError: If the request failed.
            DataProcessingError: If CDAS delivered no readable data.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        result = self.client.fetch(dataset_id, start_time, end_time)
        if result.data is None:
            logger.info("No data of %s for %s to %s.", dataset_id, start_time, end_time)
            return

        dataset_frame = dataset_to_frame(result.data, self.client.variables(dataset_id), start_time, end_time)
        cleaned_frame = clean_istp_columns(dataset_frame.frame, dataset_frame.attributes)
        documents = dataframe_to_documents(cleaned_frame, f"{SourceType.CDAWEB}/{group_name}")
        if documents:
            self._load_data(sanitize_collection_name(dataset_id), documents)

        plan = self._variable_plans.get(dataset_id)
        for skipped in [*(plan.not_stored if plan else ()), *dataset_frame.not_stored]:
            delivered.not_stored.setdefault(skipped.name, skipped)
        delivered.variable_attributes.update(dataset_frame.attributes)
        for column_name in cleaned_frame.columns:
            delivered.column_types[str(column_name)] = column_data_type(cleaned_frame[column_name])

    def _save_pipeline_metadata(self, dataset_id: str, delivered: DeliveredMetadata) -> None:
        """Saves the metadata record built from the ISTP attributes delivered with the data and the attributes themselves.

        The attributes are of kind "file", as the master file of the dataset, laid over the record as kind "istp",
        overrides them. A failing build or save is logged, as the stored data documents stay valid without metadata.

        Args:
            dataset_id: The dataset ID.
            delivered: The metadata delivered by the chunks of the run.
        """
        retrieved_at = datetime.now(UTC)
        try:
            record = DatasetMetadata(
                dataset_id=dataset_id,
                collection=sanitize_collection_name(dataset_id),
                source=SourceInfo(type=SourceType.CDAWEB),
                time=TimeInfo(timestamp_location=time_stamp_location(delivered.variable_attributes)),
                variables=self._istp_variables(delivered.variable_attributes, delivered.column_types),
                not_stored=list(delivered.not_stored.values()),
                provenance=[MetadataSource(kind=MetadataSourceKind.FILE, retrieved_at=retrieved_at)],
                updated_at=retrieved_at,
            )
            self.metadata_repository.save_raw(dataset_id, MetadataSourceKind.FILE, delivered.variable_attributes, None, retrieved_at)
            save_with_overlays(self.metadata_repository, [record])
        except METADATA_SAVE_ERRORS:
            logger.exception("Failed to save metadata of %s", dataset_id)
            return
        logger.info("Saved metadata of %s.", dataset_id)

    @staticmethod
    def _istp_variables(variable_attributes: dict[str, Any], stored_column_types: dict[str, DataType | None]) -> list[VariableMetadata]:
        """Describes each stored column by the ISTP attributes of its variable as delivered with the data.

        Args:
            variable_attributes: The attributes of each delivered variable, keyed by its name.
            stored_column_types: The value type of each stored column.

        Returns:
            One variable per stored column; a column without variable carries only its name and type.
        """
        column_sources = map_columns_to_istp_variables(stored_column_types, variable_attributes)
        variables: list[VariableMetadata] = []
        for column_name, data_type in stored_column_types.items():
            variable_name, component_index = column_sources.get(column_name, (column_name, None))
            attributes = variable_attributes.get(variable_name)
            if not isinstance(attributes, dict):
                attributes = {}
            variables.append(
                VariableMetadata.from_single_source(
                    MetadataSourceKind.FILE,
                    name=column_name,
                    source_name=variable_name,
                    component=component_index,
                    element_index=component_index,
                    # FIELDNAM is the short name every ISTP variable carries, used when CATDESC is missing
                    description=istp_text_attribute(attributes, "CATDESC") or istp_text_attribute(attributes, "FIELDNAM"),
                    label=istp_text_attribute(attributes, "LABLAXIS"),
                    dtype=data_type,
                    units=normalize_units(istp_text_attribute(attributes, "UNITS")),
                    fill=istp_scalar_attribute(attributes.get("FILLVAL")),
                    valid_min=_numeric_attribute(attributes.get("VALIDMIN")),
                    valid_max=_numeric_attribute(attributes.get("VALIDMAX")),
                    var_type=istp_text_attribute(attributes, "VAR_TYPE"),
                )
            )
        return variables


def column_data_type(column: pd.Series) -> DataType | None:
    """Names the value type of a stored column.

    Args:
        column: The column as stored, after cleaning.

    Returns:
        'boolean', 'integer', 'double' or 'string', or None for any other pandas type.
    """
    if pd.api.types.is_bool_dtype(column):
        return DataType.BOOLEAN
    if pd.api.types.is_integer_dtype(column):
        return DataType.INTEGER
    if pd.api.types.is_float_dtype(column):
        return DataType.DOUBLE
    if pd.api.types.is_string_dtype(column) or pd.api.types.is_object_dtype(column):
        return DataType.STRING
    return None


def _numeric_attribute(value: Any) -> float | None:
    """Reads a single-valued numeric ISTP attribute such as VALIDMIN.

    Args:
        value: The attribute value as delivered with the data.

    Returns:
        The value as float, or None if the attribute is missing, holds one value per component or is not numeric.
    """
    scalar = istp_scalar_attribute(value)
    if isinstance(scalar, (int, float)) and not isinstance(scalar, bool):
        return float(scalar)
    return None
