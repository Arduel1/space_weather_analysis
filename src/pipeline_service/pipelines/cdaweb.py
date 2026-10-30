import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from functools import partial
from types import MappingProxyType
from typing import Any, ClassVar

import pandas as pd
from cdasws import CdasWs
from pymongo.database import Database

from pipeline_service.database.mongo_loader import MongoLoader
from pipeline_service.parsing.cdaweb_xarray import VariablePlan, dataset_to_frame, plan_master_variables
from pipeline_service.parsing.cdf import (
    cdf_scalar_attribute,
    cdf_text_attribute,
    clean_cdf_columns,
    map_columns_to_cdf_variables,
    time_stamp_location,
)
from pipeline_service.pipelines.base import METADATA_SAVE_ERRORS, BasePipeline, ProgressCallback
from pipeline_service.pipelines.report import IngestionReport
from pipeline_service.util.documents import dataframe_to_documents
from pipeline_service.util.time import generate_time_chunks
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.clients.cdaweb import CdawebClient
from space_weather_analysis.coverage.repository import CoverageRepository, MongoCoverageRepository
from space_weather_analysis.metadata.models import (
    DatasetMetadata,
    DataType,
    MetadataSource,
    SkippedParameter,
    SourceInfo,
    TimeInfo,
    TimestampLocation,
    VariableMetadata,
)
from space_weather_analysis.metadata.normalize import normalize_units
from space_weather_analysis.metadata.overlays import save_with_overlays
from space_weather_analysis.metadata.overlays.cdaweb import cdas_dataset_id
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)


class CdawebPipeline(MongoLoader, BasePipeline):
    """Ingests CDAWeb datasets chunk by chunk from the CDAS web services.

    CDAWeb data follow the ISTP Metadata Guidelines (International Solar-Terrestrial
    Physics, https://istp-metadata.readthedocs.io/), which define the attributes of each
    variable, such as fill value (FILLVAL), valid range (VALIDMIN/VALIDMAX), unit (UNITS)
    and description (CATDESC). The pipeline uses them to clean the data and to describe it.
    """

    # Time span of one request and of one check for already stored documents
    CHUNK_DURATION = timedelta(days=7)
    # Time stamp locations from BIN_LOCATION that the metadata record names; any other becomes 'other'
    ISTP_TIMESTAMP_LOCATIONS: ClassVar[Mapping[str, TimestampLocation]] = MappingProxyType({"begin": "begin", "center": "center", "end": "end"})

    TIME_FIELD = "timestamp"

    def __init__(self, db: Database, cdas: CdasWs | None = None) -> None:
        """Initializes the pipeline.

        Args:
            db: The MongoDB database instance.
            cdas: The CDAS web service client the data are requested with; a new one if None.
        """
        super().__init__(db)
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
        """Ingests each dataset in chunks of CHUNK_DURATION, skipping chunks that already hold documents.

        Args:
            datasets: The IDs of the datasets to ingest.
            start_time: The start of the time range (inclusive).
            end_time: The end of the time range (exclusive).
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with the number of tried chunks and the chunks that failed.

        Raises:
            ValueError: If start or end time is missing, no dataset is given or a dataset has no FIDO_CDAWEB source in the master catalog.
        """
        if not start_time or not end_time:
            raise ValueError("The CDAWeb pipeline needs a start and an end time.")
        if not datasets:
            raise ValueError("The CDAWeb pipeline needs at least one dataset.")
        group_names = {dataset_id: self._find_fido_group(dataset_id) for dataset_id in datasets}
        self._variable_plans = self._plan_variables(datasets)

        time_chunks = generate_time_chunks(ensure_aware(start_time), ensure_aware(end_time), self.CHUNK_DURATION)
        report = IngestionReport()
        for dataset_index, dataset_id in enumerate(datasets):
            collection_name = sanitize_collection_name(dataset_id)
            self._ensure_timeseries_collection(collection_name, time_field=self.TIME_FIELD, meta_field="metadata")
            logger.info("Processing dataset %s (group %s) in %d chunks...", dataset_id, group_names[dataset_id], len(time_chunks))
            reported_not_stored: set[str] = set()
            processed_any_chunk = False

            for chunk_index, (chunk_start, chunk_stop) in enumerate(time_chunks):
                self._report_chunk_progress(
                    progress_callback,
                    dataset_index,
                    len(datasets),
                    chunk_index,
                    len(time_chunks),
                    f"Requesting {dataset_id}: Chunk {chunk_index + 1}/{len(time_chunks)}",
                )
                if self.db[collection_name].find_one({self.TIME_FIELD: {"$gte": chunk_start, "$lt": chunk_stop}}, {"_id": 1}):
                    logger.info("Skipping chunk %s to %s of %s: documents already stored.", chunk_start, chunk_stop, dataset_id)
                    continue

                not_stored = self._ingest_chunk(
                    report,
                    dataset_id,
                    chunk_start,
                    chunk_stop,
                    partial(self._process_chunk, dataset_id, group_names[dataset_id], chunk_start, chunk_stop),
                )
                processed_any_chunk = True

                # Reported once per dataset, as every chunk leaves out the same variables
                new_not_stored = {skipped.name for skipped in not_stored or []} - reported_not_stored
                if new_not_stored:
                    logger.warning("Dataset %s: variables not stored: %s", dataset_id, sorted(new_not_stored))
                    reported_not_stored |= new_not_stored

            if processed_any_chunk:
                self._refresh_coverage(self.coverage_repository, dataset_id, collection_name)

        return report

    def _find_fido_group(self, dataset_id: str) -> str:
        """Reads the observatory group of the first FIDO_CDAWEB source of a dataset from the master catalog.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The group name, the source_id of the source.

        Raises:
            ValueError: If the master catalog lists no FIDO_CDAWEB source with a group for the dataset.
        """
        for source in self.catalog_repository.get_sources(dataset_id, SourceType.FIDO_CDAWEB):
            group_name = source.get("source_id")
            if group_name:
                return group_name
        raise ValueError(f"The master catalog lists no {SourceType.FIDO_CDAWEB} source for dataset '{dataset_id}'.")

    def _plan_variables(self, datasets: Sequence[str]) -> dict[str, VariablePlan]:
        """Selects the variables to request for each dataset from its stored CDAWeb master file.

        Args:
            datasets: The dataset IDs.

        Returns:
            The plan of each dataset with a stored master file; datasets without one request the variables CDAS lists.
        """
        cdas_ids = sorted({cdas_dataset_id(dataset_id) for dataset_id in datasets})
        raw_masters = self.metadata_repository.get_raw_many(cdas_ids, "istp")
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

    def _process_chunk(self, dataset_id: str, group_name: str, start_time: datetime, end_time: datetime) -> list[SkippedParameter]:
        """Requests, cleans and stores one chunk of a dataset and saves its metadata.

        Args:
            dataset_id: The dataset ID.
            group_name: The observatory group of the dataset, part of the source identifier of the documents.
            start_time: The start of the chunk (inclusive).
            end_time: The end of the chunk (exclusive).

        Returns:
            The variables left out of the documents, with the reason.

        Raises:
            DataRetrievalError: If the request failed.
            DataProcessingError: If CDAS delivered no readable data.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        result = self.client.fetch(dataset_id, start_time, end_time)
        if result.data is None:
            logger.info("No data of %s for %s to %s.", dataset_id, start_time, end_time)
            return []

        dataset_frame = dataset_to_frame(result.data, self.client.variables(dataset_id), start_time, end_time)
        plan = self._variable_plans.get(dataset_id)
        not_stored = _unique_by_name([*(plan.not_stored if plan else ()), *dataset_frame.not_stored])
        cleaned_frame, _ = clean_cdf_columns(dataset_frame.frame, dataset_frame.attributes)
        documents = dataframe_to_documents(cleaned_frame, f"{SourceType.FIDO_CDAWEB}/{group_name}")
        if documents:
            self._load_data(sanitize_collection_name(dataset_id), documents)
        stored_column_types = {str(column_name): column_data_type(cleaned_frame[column_name]) for column_name in cleaned_frame.columns}
        self._save_pipeline_metadata(dataset_id, dataset_frame.attributes, stored_column_types, not_stored)
        return not_stored

    def _save_pipeline_metadata(
        self,
        dataset_id: str,
        file_metadata: dict[str, Any],
        stored_column_types: dict[str, DataType | None],
        not_stored: list[SkippedParameter],
    ) -> None:
        """Saves the metadata record built from the ISTP attributes delivered with the data and the attributes themselves.

        The attributes are of kind "file", as the master file of the dataset, laid over the record as kind "istp",
        overrides them. A failing save is logged, as the stored data documents stay valid without metadata.

        Args:
            dataset_id: The dataset ID.
            file_metadata: The attributes of each delivered variable, keyed by its name.
            stored_column_types: The value type of each stored column; empty if no column was stored.
            not_stored: The variables left out of the documents, with the reason.
        """
        retrieved_at = datetime.now(UTC)
        location = time_stamp_location(file_metadata)
        record = DatasetMetadata(
            dataset_id=dataset_id,
            collection=sanitize_collection_name(dataset_id),
            source=SourceInfo(type=SourceType.FIDO_CDAWEB),
            time=TimeInfo(timestamp_location=self.ISTP_TIMESTAMP_LOCATIONS.get(location, "other")),
            variables=self._istp_variables(file_metadata, stored_column_types),
            not_stored=not_stored,
            provenance=[MetadataSource(kind="file", retrieved_at=retrieved_at)],
            updated_at=retrieved_at,
        )
        try:
            self.metadata_repository.save_raw(dataset_id, "file", file_metadata, None, retrieved_at)
            save_with_overlays(self.metadata_repository, [record])
        except METADATA_SAVE_ERRORS:
            logger.exception("Failed to save metadata of %s", dataset_id)

    @staticmethod
    def _istp_variables(file_metadata: dict[str, Any], stored_column_types: dict[str, DataType | None]) -> list[VariableMetadata]:
        """Describes each stored column by the ISTP attributes of its variable as delivered with the data.

        Args:
            file_metadata: The attributes of each delivered variable, keyed by its name.
            stored_column_types: The value type of each stored column.

        Returns:
            One variable per stored column; a column without variable carries only its name and type.
        """
        column_sources = map_columns_to_cdf_variables(stored_column_types, file_metadata)
        variables: list[VariableMetadata] = []
        for column_name, data_type in stored_column_types.items():
            variable_name, component_index = column_sources.get(column_name, (column_name, None))
            attributes = file_metadata.get(variable_name)
            if not isinstance(attributes, dict):
                attributes = {}
            variables.append(
                VariableMetadata.from_single_source(
                    "file",
                    name=column_name,
                    source_name=variable_name,
                    component=component_index,
                    element_index=component_index,
                    # FIELDNAM is the short name every ISTP variable carries, used when CATDESC is missing
                    description=cdf_text_attribute(attributes, "CATDESC") or cdf_text_attribute(attributes, "FIELDNAM"),
                    label=cdf_text_attribute(attributes, "LABLAXIS"),
                    dtype=data_type,
                    units=normalize_units(cdf_text_attribute(attributes, "UNITS")),
                    fill=cdf_scalar_attribute(attributes.get("FILLVAL")),
                    valid_min=_numeric_attribute(attributes.get("VALIDMIN")),
                    valid_max=_numeric_attribute(attributes.get("VALIDMAX")),
                    var_type=cdf_text_attribute(attributes, "VAR_TYPE"),
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
        return "boolean"
    if pd.api.types.is_integer_dtype(column):
        return "integer"
    if pd.api.types.is_float_dtype(column):
        return "double"
    if pd.api.types.is_string_dtype(column) or pd.api.types.is_object_dtype(column):
        return "string"
    return None


def _numeric_attribute(value: Any) -> float | None:
    """Reads a single-valued numeric CDF attribute such as VALIDMIN.

    Args:
        value: The attribute value as returned by cdflib.

    Returns:
        The value as float, or None if the attribute is missing, holds one value per component or is not numeric.
    """
    scalar = cdf_scalar_attribute(value)
    if isinstance(scalar, (int, float)) and not isinstance(scalar, bool):
        return float(scalar)
    return None


def _unique_by_name(skipped_parameters: list[SkippedParameter]) -> list[SkippedParameter]:
    """Keeps the first entry of each parameter name.

    Args:
        skipped_parameters: The parameters left out, possibly named twice.

    Returns:
        The parameters with distinct names, in their first order.
    """
    unique: dict[str, SkippedParameter] = {}
    for skipped in skipped_parameters:
        unique.setdefault(skipped.name, skipped)
    return list(unique.values())
