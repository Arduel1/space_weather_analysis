import logging
import pathlib
import pickle
from collections.abc import Generator, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from types import MappingProxyType
from typing import Any, ClassVar

import gridfs
import pandas as pd
import xarray as xr
from pymongo.database import Database
from sunpy.timeseries.timeseriesbase import GenericTimeSeries

from pipeline_service.database.mongo_loader import MongoLoader
from pipeline_service.parsing.cdf import (
    MAX_ARRAY_COLUMNS,
    cdf_scalar_attribute,
    cdf_text_attribute,
    clean_cdf_columns,
    map_columns_to_cdf_variables,
    time_stamp_location,
)
from pipeline_service.parsing.file_parser import parse_datafiles
from pipeline_service.parsing.netcdf import parse_xarray_timestamp
from pipeline_service.pipelines.base import METADATA_SAVE_ERRORS, BasePipeline, ProgressCallback
from pipeline_service.pipelines.report import IngestionReport
from pipeline_service.util.documents import build_gridfs_reference_document, dataframe_to_documents
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
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)


class CdawebPipeline(MongoLoader, BasePipeline):
    """Ingests CDAWeb datasets chunk by chunk from data files downloaded with SunPy Fido.

    CDAWeb files follow the ISTP Metadata Guidelines (International Solar-Terrestrial
    Physics, https://istp-metadata.readthedocs.io/), which define the attributes of each
    variable, such as fill value (FILLVAL), valid range (VALIDMIN/VALIDMAX), unit (UNITS)
    and description (CATDESC). The pipeline uses them to clean the data and to describe it.
    """

    # Time span of one download and of one check for already stored documents
    CHUNK_DURATION = timedelta(days=7)
    # Time stamp locations from BIN_LOCATION that the metadata record names; any other becomes 'other'
    ISTP_TIMESTAMP_LOCATIONS: ClassVar[Mapping[str, TimestampLocation]] = MappingProxyType({"begin": "begin", "center": "center", "end": "end"})

    TIME_FIELD = "timestamp"

    def __init__(self, db: Database, download_dir: pathlib.Path, client: CdawebClient | None = None) -> None:
        """Initializes the pipeline.

        Args:
            db: The MongoDB database instance.
            download_dir: The directory the data files are downloaded to.
            client: The client downloading the data files; a new CdawebClient downloading to download_dir if None.
        """
        super().__init__(db)
        self.client: CdawebClient = client if client is not None else CdawebClient(download_dir)
        self.metadata_repository: MetadataRepository = MongoMetadataRepository(db)
        self.catalog_repository: CatalogRepository = MongoCatalogRepository(db)
        self.coverage_repository: CoverageRepository = MongoCoverageRepository(db)
        self.fs: gridfs.GridFS = gridfs.GridFS(self.db)

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

        time_chunks = generate_time_chunks(ensure_aware(start_time), ensure_aware(end_time), self.CHUNK_DURATION)
        report = IngestionReport()
        for dataset_index, dataset_id in enumerate(datasets):
            collection_name = sanitize_collection_name(dataset_id)
            self._ensure_timeseries_collection(collection_name, time_field=self.TIME_FIELD, meta_field="metadata")
            logger.info("Processing dataset %s (group %s) in %d chunks...", dataset_id, group_names[dataset_id], len(time_chunks))
            skipped_variables_reported = False
            processed_any_chunk = False

            for chunk_index, (chunk_start, chunk_stop) in enumerate(time_chunks):
                self._report_chunk_progress(
                    progress_callback,
                    dataset_index,
                    len(datasets),
                    chunk_index,
                    len(time_chunks),
                    f"Downloading {dataset_id}: Chunk {chunk_index + 1}/{len(time_chunks)}",
                )
                if self.db[collection_name].find_one({self.TIME_FIELD: {"$gte": chunk_start, "$lt": chunk_stop}}, {"_id": 1}):
                    logger.info("Skipping chunk %s to %s of %s: documents already stored.", chunk_start, chunk_stop, dataset_id)
                    continue

                skipped_variables = self._ingest_chunk(
                    report,
                    dataset_id,
                    chunk_start,
                    chunk_stop,
                    partial(self._process_dataset, dataset_id, group_names[dataset_id], chunk_start, chunk_stop),
                )
                processed_any_chunk = True

                # Reported once per dataset, as every chunk skips the same variables
                if skipped_variables and not skipped_variables_reported:
                    logger.warning(
                        "Dataset %s: arrays with more than %d elements are not stored: %s", dataset_id, MAX_ARRAY_COLUMNS, skipped_variables
                    )
                    skipped_variables_reported = True

            # 1D documents and the GridFS reference documents of N-D data both carry their time in the time field
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

    def _process_dataset(self, dataset_id: str, group_name: str, start_time: datetime, end_time: datetime) -> list[str]:
        """Downloads, parses and stores one chunk of a dataset and saves its metadata.

        Args:
            dataset_id: The dataset ID.
            group_name: The observatory group of the dataset, part of the source identifier of the documents.
            start_time: The start of the chunk (inclusive).
            end_time: The end of the chunk (exclusive).

        Returns:
            The names of the variables left out because they have too many components for columns.

        Raises:
            DataRetrievalError: If the download failed.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        result = self.client.fetch(dataset_id, start_time, end_time)
        if result.data is None:
            logger.info("No data file of %s for %s to %s.", dataset_id, start_time, end_time)
            return []

        opened_datasets: list[xr.Dataset] = []
        skipped_variables: list[str] = []
        stored_column_types: dict[str, DataType | None] = {}
        try:
            data_object, file_metadata = parse_datafiles([str(path) for path in result.data], start_time, end_time, variables_to_parse=[])
            collection_name = sanitize_collection_name(dataset_id)
            source_id = f"{SourceType.FIDO_CDAWEB}/{group_name}"

            if isinstance(data_object, GenericTimeSeries):
                stored_column_types, skipped_variables = self._handle_1d_data(data_object, collection_name, source_id, file_metadata)
            elif isinstance(data_object, list):
                self._load_nd_data_to_gridfs(data_object, collection_name, source_id, opened_datasets)
            else:
                logger.info("No 1D or N-D data could be parsed for %s in this chunk.", dataset_id)

            if file_metadata:
                self._save_pipeline_metadata(dataset_id, file_metadata, stored_column_types, skipped_variables)
        finally:
            # Releases the file handles of the opened xarray datasets
            for opened_dataset in opened_datasets:
                opened_dataset.close()
        return skipped_variables

    def _handle_1d_data(
        self,
        data_object: GenericTimeSeries,
        collection_name: str,
        source_id: str,
        file_metadata: dict[str, Any],
    ) -> tuple[dict[str, DataType | None], list[str]]:
        """Cleans, transforms and loads 1D TimeSeries data into MongoDB.

        Args:
            data_object: The parsed 1D timeseries.
            collection_name: The target MongoDB collection name.
            source_id: The source identifier string.
            file_metadata: The attributes of each CDF variable, which define fill values and valid ranges.

        Returns:
            The value type of each stored column and the names of the variables left out because they have too many components for columns.

        Raises:
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        df_to_load, skipped_variables = clean_cdf_columns(data_object.to_dataframe(), file_metadata)
        documents = dataframe_to_documents(df_to_load, source_id)
        self._load_data(collection_name, documents)
        stored_column_types = {str(column_name): column_data_type(df_to_load[column_name]) for column_name in df_to_load.columns}
        return stored_column_types, skipped_variables

    def _load_nd_data_to_gridfs(
        self,
        data_object: list[tuple[xr.Dataset, str, str]],
        collection_name: str,
        source_id: str,
        opened_datasets: list[xr.Dataset],
    ) -> None:
        """Loads N-D data by processing files sequentially and storing payloads in GridFS.

        Args:
            data_object: List of (dataset, time_dim, time_coord).
            collection_name: MongoDB collection name.
            source_id: Source identifier string.
            opened_datasets: List to track opened datasets for cleanup.
        """
        logger.info("N-D data for %s parsed. Storing payloads in GridFS...", collection_name)
        opened_datasets.extend([ds for ds, _, _ in data_object])

        total_loaded = 0
        n_files = len(data_object)

        for i, (ds_file, time_dim, time_coord) in enumerate(data_object):
            logger.info("Processing file %d/%d", i + 1, n_files)
            try:
                logger.info("Found time dimension %s (coordinate %s)", time_dim, time_coord)

                documents_batch: list[dict[str, Any]] = []
                for doc in self._transform_xarray_to_docs(ds_file, source_id, time_dim, time_coord, collection_name):
                    documents_batch.append(doc)
                    if len(documents_batch) >= self.DEFAULT_BATCH_SIZE:
                        self._load_data(collection_name, documents_batch)
                        total_loaded += len(documents_batch)
                        documents_batch.clear()

                if documents_batch:
                    self._load_data(collection_name, documents_batch)
                    total_loaded += len(documents_batch)

            except Exception:
                logger.exception("Failed to process file %d", i + 1)

        logger.info("N-D data processing complete. Documents (pointers) loaded: %d", total_loaded)

    def _transform_xarray_to_docs(
        self,
        ds: xr.Dataset,
        source_info: str,
        time_dim: str,
        time_coord: str,
        collection_name: str,
    ) -> Generator[dict[str, Any], None, None]:
        """Generator to convert an N-D Dataset into MongoDB documents.

        Iterates through the time dimension, serializes each time slice using pickle,
        stores it in GridFS, and yields a metadata document referencing the GridFS file.

        Args:
            ds: The xarray dataset.
            source_info: Source identifier.
            time_dim: The name of the time dimension.
            time_coord: The name of the time coordinate.
            collection_name: Target collection name.

        Yields:
            A dictionary representing a MongoDB document.
        """
        for i in range(len(ds[time_dim])):
            ts_slice = ds.isel({time_dim: i})

            # Use the external helper to parse timestamp
            timestamp = parse_xarray_timestamp(ts_slice, time_coord, i, allow_fallback=False)

            if timestamp is None:
                logger.warning("Timestamp parse returned None for slice index %d, time coordinate %s. Skipping slice.", i, time_coord)
                continue

            timestamp = ensure_aware(timestamp)

            data_payload: dict[str, Any] = {}
            for var_name in ts_slice.data_vars:
                try:
                    var_data = ts_slice[var_name]
                    data_payload[str(var_name)] = var_data.values.tolist()
                except Exception as e:
                    # A variable that fails to serialize is left out of the payload, the others are kept
                    logger.debug("Variable %s at %s not serializable, left out of the payload: %s", var_name, timestamp, e)

            try:
                payload_bytes = pickle.dumps(data_payload)

                # Store large payload in GridFS
                gridfs_id = self.fs.put(
                    payload_bytes,
                    filename=f"{ds.attrs.get('Dataset_ID', 'data')}_{timestamp.isoformat()}.pkl",
                    encoding="binary",
                    timestamp=timestamp,
                    metadata={"collection": collection_name},
                )
            except Exception:
                logger.exception("Failed to write payload to GridFS for %s", timestamp)
                continue

            yield build_gridfs_reference_document(timestamp, gridfs_id, source_info)

    def _save_pipeline_metadata(
        self,
        dataset_id: str,
        file_metadata: dict[str, Any],
        stored_column_types: dict[str, DataType | None],
        skipped_variables: list[str],
    ) -> None:
        """Saves the metadata record built from the ISTP attributes of the data files and the attributes themselves.

        The attributes are of kind "file", as the master file of the dataset, laid over the record as kind "istp",
        overrides them. A failing save is logged, as the stored data documents stay valid without metadata.

        Args:
            dataset_id: The dataset ID.
            file_metadata: The attributes of each file variable, keyed by its name.
            stored_column_types: The value type of each stored column; empty if no column was stored.
            skipped_variables: The names of the variables not stored because they have too many components.
        """
        retrieved_at = datetime.now(UTC)
        location = time_stamp_location(file_metadata)
        record = DatasetMetadata(
            dataset_id=dataset_id,
            collection=sanitize_collection_name(dataset_id),
            source=SourceInfo(type=SourceType.FIDO_CDAWEB),
            time=TimeInfo(timestamp_location=self.ISTP_TIMESTAMP_LOCATIONS.get(location, "other")),
            variables=self._istp_variables(file_metadata, stored_column_types),
            not_stored=[SkippedParameter(name=name, reason=f"array with more than {MAX_ARRAY_COLUMNS} elements") for name in skipped_variables],
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
        """Describes each stored column by the ISTP attributes of its CDF variable in the data files.

        Args:
            file_metadata: The attributes of each CDF variable, keyed by its name.
            stored_column_types: The value type of each stored column.

        Returns:
            One variable per stored column; a column without CDF variable carries only its name and type.
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
