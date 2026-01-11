import logging
import pathlib
import pickle
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, List, Optional, Tuple, cast

import gridfs
import pandas as pd
import xarray as xr
from pymongo.database import Database
from sunpy.timeseries.timeseriesbase import GenericTimeSeries

from common.clients.cdaweb import CdawebClient
from common.util.netcdf_utils import parse_xarray_timestamp
from pipeline_service.database.mongo_loader import MongoLoader
from pipeline_service.metadata.generator import MetadataWriter
from pipeline_service.metadata.models import VariableMetadata
from pipeline_service.parsing.file_parser import parse_datafiles
from pipeline_service.pipelines.base import BasePipeline
from pipeline_service.util.naming import sanitize_collection_name
from pipeline_service.util.time import generate_time_chunks

logger = logging.getLogger(__name__)


class CdawebPipeline(MongoLoader, BasePipeline):
    """
    Orchestrates the process of fetching, downloading, parsing,
    and documenting data from the CDAWeb service via SunPy/Fido.

    This pipeline relies on the MongoDB 'master_catalog' collection
    to discover data sources (Observatory Groups) and processes data
    in time chunks to handle large datasets.
    """

    CATALOG_COLLECTION = "master_catalog"
    SOURCE_ID = "FIDO_CDAWEB"

    def __init__(self, db: Database, base_data_path: pathlib.Path):
        """
        Initializes the pipeline.

        Args:
            db: The MongoDB database instance.
            base_data_path: The root directory for data downloads.
        """
        super().__init__(db)
        self.client: CdawebClient = CdawebClient()
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
    ) -> None:
        """
        Runs the complete data pipeline orchestration.

        This method iterates through the specified time range in 7-day
        chunks to avoid failures on large requests with data gaps.
        It checks if data for a chunk already exists before fetching.

        Args:
            datasets: A list of dataset IDs to fetch.
            start_time: The start time for the data query.
            end_time: The end time for the data query.
        """
        if not start_time or not end_time:
            logger.error("Start time and end time are required for CDAWeb pipeline.")
            return

        # Ensure timezone awareness
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        logger.info("Starting CDAWeb Pipeline")
        logger.info(f"Time Range: {start_time} to {end_time}")

        if not datasets:
            logger.warning("No datasets specified in 'datasets'.")
            return

        chunk_delta = timedelta(days=7)
        time_chunks = generate_time_chunks(start_time, end_time, chunk_delta)
        time_field = "timestamp"

        for dataset_id in datasets:
            group_name = self._get_fido_source_from_db(dataset_id)
            if not group_name:
                logger.error(f"Skipping {dataset_id}: Could not find its {self.SOURCE_ID} group in catalog.")
                continue

            collection_name = sanitize_collection_name(dataset_id)

            # Ensure the target collection exists with time-series options
            self._ensure_timeseries_collection(collection_name, time_field=time_field, meta_field="metadata")

            logger.info(f"Processing dataset {dataset_id} (group {group_name}) for {len(time_chunks)} chunks...")

            for i, (chunk_start, chunk_stop) in enumerate(time_chunks):
                logger.info(f"Checking chunk {i + 1}/{len(time_chunks)}: {chunk_start} to {chunk_stop}")

                # Check if data for this chunk already exists in DB
                query = {time_field: {"$gte": chunk_start, "$lt": chunk_stop}}
                if self.db[collection_name].find_one(query, {"_id": 1}):
                    logger.info("Skipping chunk: Data already exists in DB.")
                    continue

                logger.info("Processing chunk: No data found in DB.")
                self._process_dataset(dataset_id, group_name, chunk_start, chunk_stop)

        logger.info("CDAWeb Pipeline finished.")

    def _get_fido_source_from_db(self, dataset_id: str) -> Optional[str]:
        """
        Queries the master_catalog to find the source_id.

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
    ) -> None:
        """
        Runs the full pipeline for a single dataset chunk.

        Args:
            dataset_id: The dataset ID to process.
            group_name: The group name (for download path).
            start_time: The start time for the query chunk.
            end_time: The end time for the query chunk.
        """
        logger.info(f"Processing Dataset Chunk: {dataset_id} ({start_time} to {end_time})")

        opened_datasets: List[xr.Dataset] = []
        try:
            # We construct the download path here to pass it to the client
            download_dir = self.base_data_dir / group_name / dataset_id
            download_dir.mkdir(parents=True, exist_ok=True)

            downloaded_files = self.client.get_data(dataset_id, start_time, end_time, download_path=download_dir)

            if not downloaded_files:
                logger.warning(f"Skipping parse/metadata for {dataset_id}: No files downloaded.")
                return

            # Convert Path objects to strings if parser expects strings
            file_paths = [str(p) for p in downloaded_files]

            data_object, file_metadata = parse_datafiles(file_paths, start_time, end_time, variables_to_parse=[])

            collection_name = sanitize_collection_name(dataset_id)
            source_id = f"{self.SOURCE_ID}/{group_name}"

            if data_object is not None:
                if isinstance(data_object, GenericTimeSeries):
                    self._handle_1d_data(data_object, collection_name, source_id)
                elif isinstance(data_object, list):
                    self._load_nd_data_to_gridfs(data_object, collection_name, source_id, opened_datasets)
                else:
                    logger.warning(f"Data object for {dataset_id} was not a recognized type ({type(data_object)}). Cannot load.")
            else:
                logger.info(f"No 1D or N-D data could be parsed for {dataset_id} in this chunk.")

            if file_metadata:
                self._save_pipeline_metadata(dataset_id, group_name, file_metadata)

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

    def _handle_1d_data(
        self,
        data_object: GenericTimeSeries,
        collection_name: str,
        source_id: str,
    ) -> None:
        """
        Transforms and loads 1D TimeSeries data into MongoDB.

        Args:
            data_object: The parsed 1D timeseries.
            collection_name: The target MongoDB collection name.
            source_id: The source identifier string.
        """
        logger.info(f"1D data for {collection_name} parsed. Transforming for MongoDB load...")
        df_to_load = data_object.to_dataframe()
        documents = self._transform_dataframe_to_docs(df_to_load, source_id)

        self._load_data(collection_name, documents)
        logger.info(f"Successfully loaded {len(documents)} documents into collection '{collection_name}'")

    def _transform_dataframe_to_docs(self, df: pd.DataFrame, source_info: str) -> List[Dict[str, Any]]:
        """
        Converts a 1D TimeSeries DataFrame into a list of MongoDB documents.

        Args:
            df: The DataFrame (from a TimeSeries) to transform.
            source_info: A string identifying the data source.

        Returns:
            A list of formatted MongoDB documents.
        """
        logger.info(f"Transforming {len(df)} DataFrame rows to MongoDB documents...")

        data_payloads = df.to_dict("records")
        timestamps = cast(pd.DatetimeIndex, df.index).to_pydatetime()
        metadata = {"source": source_info}

        documents = [
            {
                "timestamp": ts_py,
                "metadata": metadata,
                "data": {str(k): v for k, v in data.items() if not pd.isna(v)},
            }
            for ts_py, data in zip(timestamps, data_payloads, strict=False)
        ]
        return documents

    def _load_nd_data_to_gridfs(
        self,
        data_object: List[Tuple[xr.Dataset, str, str]],
        collection_name: str,
        source_id: str,
        opened_datasets: List[xr.Dataset],
    ) -> None:
        """
        Loads N-D data by processing files sequentially and storing payloads in GridFS.

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
        """
        Generator to convert an N-D Dataset into MongoDB documents.

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

            # Debugging logs for time coordinate presence omitted for brevity in final implementation
            # but logic is preserved below

            # Use the external helper to parse timestamp
            timestamp = parse_xarray_timestamp(ts_slice, time_coord, i, allow_fallback=False)

            if timestamp is None:
                logger.warning(f"Timestamp parse returned None for slice index={i}, time_coord='{time_coord}'. Skipping slice.")
                continue

            # Ensure timestamp is aware
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            data_payload: Dict[str, Any] = {}
            for var_name in ts_slice.data_vars:
                try:
                    var_data = ts_slice[var_name]
                    data_payload[str(var_name)] = var_data.values.tolist()
                except Exception:
                    # Skip individual variables that fail to serialize
                    pass

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

            yield {
                "timestamp": timestamp,
                "metadata": {"source": source_info},
                "data_gridfs_id": gridfs_id,
            }

    def _save_pipeline_metadata(
        self,
        dataset_id: str,
        group_name: str,
        file_metadata: Dict[str, Any],
    ) -> None:
        """
        Normalizes and saves the pipeline metadata to the database.

        Args:
            dataset_id: The dataset ID.
            group_name: The source group name.
            file_metadata: The metadata parsed from the file.
        """
        # We pass an empty list for API meta as this specific flow relies on file metadata
        variables = self._normalize_cdaweb_meta_to_variables([], file_metadata)
        source_info = {
            "Source Client": self.SOURCE_ID,
            "Source Group": group_name,
        }
        self.metadata_writer.save_metadata_to_db(
            dataset_id=dataset_id,
            variables=variables,
            source_info=source_info,
        )

    def _normalize_cdaweb_meta_to_variables(self, api_meta: List[Dict[str, Any]], file_meta: Dict[str, Any]) -> List[VariableMetadata]:
        """
        Merges file and API metadata into VariableMetadata objects.

        Args:
            api_meta: Metadata from an API source.
            file_meta: Metadata parsed from a data file.

        Returns:
            A merged list of metadata objects.
        """
        variables: List[VariableMetadata] = []
        api_meta_dict = {var["Name"]: var for var in api_meta if var.get("Name")}
        all_var_names = set(api_meta_dict.keys()) | set(file_meta.keys())

        for var_name in all_var_names:
            if not var_name:
                continue

            api_attrs = api_meta_dict.get(var_name, {})
            file_attrs = file_meta.get(var_name, {})

            variables.append(
                VariableMetadata(
                    name=var_name,
                    description=api_attrs.get("LongDescription"),
                    units=api_attrs.get("Units"),
                    fill=api_attrs.get("Fill"),
                    type=api_attrs.get("Type"),
                    file_attributes=file_attrs,
                )
            )
        return variables
