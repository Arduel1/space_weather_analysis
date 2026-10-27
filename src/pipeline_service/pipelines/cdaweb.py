import logging
import pathlib
import pickle
from datetime import datetime, timedelta
from typing import Any, Dict, Generator, List, Optional, Tuple

import gridfs
import xarray as xr
from pymongo.database import Database
from sunpy.timeseries.timeseriesbase import GenericTimeSeries

from pipeline_service.database.mongo_loader import MongoLoader
from pipeline_service.metadata.generator import MetadataWriter
from pipeline_service.metadata.models import VariableMetadata
from pipeline_service.parsing.cdf import MAX_ARRAY_COLUMNS, cdf_scalar_attribute, cdf_text_attribute, clean_cdf_columns, time_stamp_location
from pipeline_service.parsing.file_parser import parse_datafiles
from pipeline_service.pipelines.base import BasePipeline, ProgressCallback
from pipeline_service.util.documents import build_gridfs_reference_document, dataframe_to_documents
from pipeline_service.util.naming import sanitize_collection_name
from pipeline_service.util.time import generate_time_chunks
from space_weather_analysis.clients.cdaweb import CdawebClient
from space_weather_analysis.util.netcdf_utils import parse_xarray_timestamp
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)


class CdawebPipeline(MongoLoader, BasePipeline):
    """Fetches, downloads, parses and documents CDAWeb data via SunPy/Fido.

    This pipeline relies on the MongoDB 'master_catalog' collection
    to discover data sources (Observatory Groups) and processes data
    in time chunks to handle large datasets.

    CDAWeb files follow the ISTP Metadata Guidelines (International Solar-Terrestrial
    Physics, https://istp-metadata.readthedocs.io/), which define the attributes of each
    variable, such as fill value (FILLVAL), valid range (VALIDMIN/VALIDMAX), unit (UNITS)
    and description (CATDESC). The pipeline uses them to clean the data and to describe it.
    """

    CATALOG_COLLECTION = "master_catalog"
    SOURCE_ID = "FIDO_CDAWEB"
    # Time span of one download and of one check for already stored documents
    CHUNK_DURATION = timedelta(days=7)

    def __init__(self, db: Database, base_data_path: pathlib.Path, client: Optional[CdawebClient] = None):
        """Initializes the pipeline.

        Args:
            db: The MongoDB database instance.
            base_data_path: The root directory for data downloads.
            client: The client downloading the data files; a new CdawebClient if None.
        """
        super().__init__(db)
        self.client: CdawebClient = client if client is not None else CdawebClient()
        self.base_data_dir: pathlib.Path = base_data_path
        self.metadata_writer: MetadataWriter = MetadataWriter(db)
        self.fs: gridfs.GridFS = gridfs.GridFS(self.db)

        if not self.base_data_dir.exists():
            self.base_data_dir.mkdir(parents=True)
            logger.info(f"Created base data directory: {self.base_data_dir}")
        logger.info(f"Pipeline initialized. Data root: {self.base_data_dir}")

    def run(
        self,
        datasets: List[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> None:
        """Runs the complete data pipeline orchestration.

        Iterates through time chunks and reports progress.

        Args:
            datasets: A list of dataset IDs to fetch.
            start_time: The start time for the data query.
            end_time: The end time for the data query.
            progress_callback: Optional callback to report progress and status.
        """
        if not start_time or not end_time:
            logger.error("Start time and end time are required for CDAWeb pipeline.")
            return

        start_time = ensure_aware(start_time)
        end_time = ensure_aware(end_time)

        logger.info("Starting CDAWeb Pipeline")
        logger.info(f"Time Range: {start_time} to {end_time}")

        if not datasets:
            logger.warning("No datasets specified in 'datasets'.")
            return

        time_chunks = generate_time_chunks(start_time, end_time, self.CHUNK_DURATION)
        total_chunks = len(time_chunks)
        time_field = "timestamp"

        total_datasets = len(datasets)

        for i, dataset_id in enumerate(datasets):
            group_name = self._get_fido_source_from_db(dataset_id)
            if not group_name:
                logger.error(f"Skipping {dataset_id}: Could not find its {self.SOURCE_ID} group in catalog.")
                continue

            collection_name = sanitize_collection_name(dataset_id)

            # Ensure the target collection exists with time-series options
            self._ensure_timeseries_collection(collection_name, time_field=time_field, meta_field="metadata")

            logger.info(f"Processing dataset {dataset_id} (group {group_name}) for {total_chunks} chunks...")
            skipped_variables_reported = False

            for j, (chunk_start, chunk_stop) in enumerate(time_chunks):
                self._report_chunk_progress(
                    progress_callback, i, total_datasets, j, total_chunks, f"Downloading {dataset_id}: Chunk {j + 1}/{total_chunks}"
                )

                logger.info(f"Checking chunk {j + 1}/{total_chunks}: {chunk_start} to {chunk_stop}")

                # Check if data for this chunk already exists in DB
                query = {time_field: {"$gte": chunk_start, "$lt": chunk_stop}}
                if self.db[collection_name].find_one(query, {"_id": 1}):
                    logger.info("Skipping chunk: Data already exists in DB.")
                    continue

                logger.info("Processing chunk: No data found in DB.")
                skipped_variables = self._process_dataset(dataset_id, group_name, chunk_start, chunk_stop)

                # Reported once per dataset, as every chunk skips the same variables
                if skipped_variables and not skipped_variables_reported:
                    logger.warning(f"Dataset {dataset_id}: arrays with more than {MAX_ARRAY_COLUMNS} elements are not stored: {skipped_variables}")
                    skipped_variables_reported = True

        logger.info("CDAWeb Pipeline finished.")

    def _get_fido_source_from_db(self, dataset_id: str) -> Optional[str]:
        """Queries the master_catalog to find the source_id.

        Args:
            dataset_id: The dataset ID to query.

        Returns:
            The source_id (group name) or None if not found.
        """
        logger.info(f"Querying catalog for {self.SOURCE_ID} source for: {dataset_id}")
        try:
            collection = self.db[self.CATALOG_COLLECTION]
            doc = collection.find_one({"dataset_id": dataset_id})

            if not doc:
                logger.error(f"Dataset '{dataset_id}' not found in {self.CATALOG_COLLECTION}")
                return None

            sources = doc.get("sources", [])

            for source in sources:
                if source.get("type") == self.SOURCE_ID:
                    source_id = source.get("source_id")
                    if source_id:
                        logger.info(f"Found FIDO source for {dataset_id}: {source_id}")
                        return source_id

            logger.warning(f"No {self.SOURCE_ID} type source found for {dataset_id}")
            return None
        except Exception as e:
            logger.error(f"Error querying catalog for {dataset_id}: {e}", exc_info=True)
            return None

    def _process_dataset(
        self,
        dataset_id: str,
        group_name: str,
        start_time: datetime,
        end_time: datetime,
    ) -> List[str]:
        """Runs the full pipeline for a single dataset chunk.

        Args:
            dataset_id: The dataset ID to process.
            group_name: The group name (for download path).
            start_time: The start time for the query chunk (inclusive).
            end_time: The end time for the query chunk (exclusive).

        Returns:
            The names of the variables left out because they have too many components for columns.
        """
        logger.info(f"Processing Dataset Chunk: {dataset_id} ({start_time} to {end_time})")

        opened_datasets: List[xr.Dataset] = []
        skipped_variables: List[str] = []
        try:
            # We construct the download path here to pass it to the client
            download_dir = self.base_data_dir / group_name / dataset_id
            download_dir.mkdir(parents=True, exist_ok=True)

            downloaded_files = self.client.get_data(dataset_id, start_time, end_time, download_path=download_dir)

            if not downloaded_files:
                logger.warning(f"Skipping parse/metadata for {dataset_id}: No files downloaded.")
                return skipped_variables

            # Convert Path objects to strings if parser expects strings
            file_paths = [str(p) for p in downloaded_files]

            data_object, file_metadata = parse_datafiles(file_paths, start_time, end_time, variables_to_parse=[])

            collection_name = sanitize_collection_name(dataset_id)
            source_id = f"{self.SOURCE_ID}/{group_name}"

            if data_object is not None:
                if isinstance(data_object, GenericTimeSeries):
                    skipped_variables = self._handle_1d_data(data_object, collection_name, source_id, file_metadata)
                elif isinstance(data_object, list):
                    self._load_nd_data_to_gridfs(data_object, collection_name, source_id, opened_datasets)
                else:
                    logger.warning(f"Data object for {dataset_id} was not a recognized type ({type(data_object)}). Cannot load.")
            else:
                logger.info(f"No 1D or N-D data could be parsed for {dataset_id} in this chunk.")

            if file_metadata:
                self._save_pipeline_metadata(dataset_id, group_name, file_metadata, skipped_variables)

        except Exception as e:
            logger.error(f"Pipeline step failed for dataset chunk {dataset_id}: {e}", exc_info=True)
        finally:
            # Cleanup opened xarray datasets to release file handles
            for ds in opened_datasets:
                try:
                    ds.close()
                except Exception as e_close:
                    logger.warning(f"Failed to close xarray dataset: {e_close}")
            logger.info(f"Finished Dataset Chunk: {dataset_id}")
        return skipped_variables

    def _handle_1d_data(
        self,
        data_object: GenericTimeSeries,
        collection_name: str,
        source_id: str,
        file_metadata: Dict[str, Any],
    ) -> List[str]:
        """Cleans, transforms and loads 1D TimeSeries data into MongoDB.

        Args:
            data_object: The parsed 1D timeseries.
            collection_name: The target MongoDB collection name.
            source_id: The source identifier string.
            file_metadata: The attributes of each CDF variable, which define fill values and valid ranges.

        Returns:
            The names of the variables left out because they have too many components for columns.
        """
        logger.info(f"1D data for {collection_name} parsed. Transforming for MongoDB load...")
        df_to_load, skipped_variables = clean_cdf_columns(data_object.to_dataframe(), file_metadata)
        logger.info(f"Transforming {len(df_to_load)} DataFrame rows to MongoDB documents...")
        documents = dataframe_to_documents(df_to_load, source_id)

        self._load_data(collection_name, documents)
        logger.info(f"Successfully loaded {len(documents)} documents into collection '{collection_name}'")
        return skipped_variables

    def _load_nd_data_to_gridfs(
        self,
        data_object: List[Tuple[xr.Dataset, str, str]],
        collection_name: str,
        source_id: str,
        opened_datasets: List[xr.Dataset],
    ) -> None:
        """Loads N-D data by processing files sequentially and storing payloads in GridFS.

        Args:
            data_object: List of (dataset, time_dim, time_coord).
            collection_name: MongoDB collection name.
            source_id: Source identifier string.
            opened_datasets: List to track opened datasets for cleanup.
        """
        logger.info(f"N-D data for {collection_name} parsed. Storing payloads in GridFS...")
        opened_datasets.extend([ds for ds, _, _ in data_object])

        total_loaded = 0
        n_files = len(data_object)

        for i, (ds_file, time_dim, time_coord) in enumerate(data_object):
            logger.info(f"Processing file {i + 1}/{n_files}")
            try:
                logger.info(f"Found time dimension: '{time_dim}' (coord: '{time_coord}')")

                documents_batch: List[Dict[str, Any]] = []
                for doc in self._transform_xarray_to_docs(ds_file, source_id, time_dim, time_coord, collection_name):
                    documents_batch.append(doc)
                    if len(documents_batch) >= self.DEFAULT_BATCH_SIZE:
                        self._load_data(collection_name, documents_batch)
                        total_loaded += len(documents_batch)
                        documents_batch.clear()

                if documents_batch:
                    self._load_data(collection_name, documents_batch)
                    total_loaded += len(documents_batch)

            except Exception as e:
                logger.error(f"Failed to process file {i + 1}: {e}", exc_info=True)

        logger.info(f"N-D data processing complete. Documents (pointers) loaded: {total_loaded}")

    def _transform_xarray_to_docs(
        self,
        ds: xr.Dataset,
        source_info: str,
        time_dim: str,
        time_coord: str,
        collection_name: str,
    ) -> Generator[Dict[str, Any], None, None]:
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
                logger.warning(f"Timestamp parse returned None for slice index={i}, time_coord='{time_coord}'. Skipping slice.")
                continue

            timestamp = ensure_aware(timestamp)

            data_payload: Dict[str, Any] = {}
            for var_name in ts_slice.data_vars:
                try:
                    var_data = ts_slice[var_name]
                    data_payload[str(var_name)] = var_data.values.tolist()
                except Exception as e:
                    # A variable that fails to serialize is left out of the payload, the others are kept
                    logger.debug(f"Variable '{var_name}' at {timestamp} not serializable, left out of the payload: {e}")

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
            except Exception as e:
                logger.error(
                    f"Failed to write payload to GridFS for {timestamp}: {e}",
                    exc_info=True,
                )
                continue

            yield build_gridfs_reference_document(timestamp, gridfs_id, source_info)

    def _save_pipeline_metadata(
        self,
        dataset_id: str,
        group_name: str,
        file_metadata: Dict[str, Any],
        skipped_variables: List[str],
    ) -> None:
        """Normalizes and saves the pipeline metadata to the database.

        Args:
            dataset_id: The dataset ID.
            group_name: The source group name.
            file_metadata: The metadata parsed from the file.
            skipped_variables: The names of the variables not stored because they have too many components.
        """
        variables = self._normalize_cdaweb_meta_to_variables(file_metadata)
        source_info = {
            "Source Client": self.SOURCE_ID,
            "Source Group": group_name,
            "Time Stamp Location": time_stamp_location(file_metadata),
            "Parameters Not Stored": skipped_variables,
        }
        self.metadata_writer.save_metadata_to_db(
            dataset_id=dataset_id,
            variables=variables,
            source_info=source_info,
        )

    def _normalize_cdaweb_meta_to_variables(self, file_meta: Dict[str, Any]) -> List[VariableMetadata]:
        """Converts the ISTP attributes of each file variable into VariableMetadata objects.

        Args:
            file_meta: Metadata parsed from a data file, the attributes of each variable keyed by its name.

        Returns:
            One metadata object per variable, with description, units and fill value taken from the attributes.
        """
        variables: List[VariableMetadata] = []
        for var_name, file_attrs in file_meta.items():
            if not var_name or not isinstance(file_attrs, dict):
                continue

            variables.append(
                VariableMetadata(
                    name=var_name,
                    # FIELDNAM is the short name every ISTP variable carries, used when CATDESC is missing
                    description=cdf_text_attribute(file_attrs, "CATDESC") or cdf_text_attribute(file_attrs, "FIELDNAM"),
                    units=cdf_text_attribute(file_attrs, "UNITS"),
                    fill=cdf_scalar_attribute(file_attrs.get("FILLVAL")),
                    file_attributes=file_attrs,
                )
            )
        return variables
