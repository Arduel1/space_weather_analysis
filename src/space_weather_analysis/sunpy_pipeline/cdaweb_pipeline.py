import logging
import pathlib
import pickle
from datetime import datetime, timedelta
from typing import Any, Dict, Generator, Hashable, List, Optional, Tuple, Union, cast

import gridfs
import numpy as np
import pandas as pd
import xarray as xr
from astropy.time import Time
from bson.objectid import ObjectId
from parfive.results import Results as ParfiveResults
from pymongo.database import Database
from sunpy.net import Fido
from sunpy.net import attrs as a
from sunpy.net.cdaweb import attrs as cdaweb_attrs
from sunpy.net.fido_factory import UnifiedResponse
from sunpy.timeseries.timeseriesbase import GenericTimeSeries

from space_weather_analysis.config import FIDO_CDAWEB_SOURCE_ID
from space_weather_analysis.database.mongo_loader_base import MongoLoaderBase
from space_weather_analysis.sunpy_pipeline.cdaweb_client import CdawebClient
from space_weather_analysis.util.data_preprocessing import generate_time_chunks, sanitize_collection_name
from space_weather_analysis.util.file_parser import parse_datafiles
from space_weather_analysis.util.metadata.metadata_generator import MetadataWriter
from space_weather_analysis.util.metadata.variable_metadata import VariableMetadata

logger = logging.getLogger(__name__)


class CdawebPipeline(MongoLoaderBase):
    """
    Orchestrates the process of fetching, downloading, parsing,
    and documenting data from a file-based data service.

    This pipeline relies on the MongoDB 'master_catalog' collection
    to discover data sources.
    """

    CATALOG_COLLECTION = "master_catalog"

    def __init__(self, db: Database, base_data_path: pathlib.Path):
        """
        Initializes the pipeline.

        Args:
            db (Database): The MongoDB database instance.
            base_data_path (pathlib.Path): The root directory for data downloads.
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

    def _get_fido_source_from_db(self, dataset_id: str) -> Optional[str]:
        """
        Queries the master_catalog to find the 'FIDO_CDAWEB_SOURCE_ID' source_id.

        Args:
            dataset_id (str): The dataset ID to query.

        Returns:
            Optional[str]: The source_id (group name) or None if not found.
        """
        logger.info(f"Querying catalog for {FIDO_CDAWEB_SOURCE_ID} source for: {dataset_id}")
        try:
            collection = self.db[self.CATALOG_COLLECTION]
            doc: Optional[Dict[str, Any]] = collection.find_one({"dataset_id": dataset_id})

            if not doc:
                logger.error(f"Dataset '{dataset_id}' not found in {self.CATALOG_COLLECTION}")
                return None

            for source in doc.get("sources", []):
                if source.get("type") == FIDO_CDAWEB_SOURCE_ID:
                    source_id: Optional[str] = source.get("source_id")
                    if source_id:
                        logger.info(f"Found FIDO source for {dataset_id}: {source_id}")
                        return source_id

            logger.warning(f"No {FIDO_CDAWEB_SOURCE_ID} type source found for {dataset_id}")
            return None
        except Exception as e:
            logger.error(f"Error querying catalog for {dataset_id}: {e}", exc_info=True)
            return None

    def _fetch_data_for_dataset(
        self,
        group_name: str,
        target_dataset: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Tuple[List[str], Optional[pathlib.Path]]:
        """
        Searches Fido for a dataset within a time range and downloads files.

        Args:
            group_name (str): The group name for creating the download path.
            target_dataset (str): The dataset ID to search for.
            start_time (datetime): The start of the time range.
            end_time (datetime): The end of the time range.

        Returns:
            Tuple[List[str], Optional[pathlib.Path]]: A list of downloaded
            file paths and the specific download directory.
        """
        logger.info(f"Searching Fido for '{target_dataset}' from {start_time} to {end_time}...")

        try:
            specific_download_dir = self.base_data_dir / group_name / target_dataset
            time_query = a.Time(start_time, end_time)
            dataset_query = cdaweb_attrs.Dataset(target_dataset)

            query_response: UnifiedResponse = Fido.search(time_query, dataset_query)

            if not query_response:
                logger.warning(f"Fido.search returned 0 results for '{target_dataset}' in chunk.")
                return [], None

            logger.info(f"Fido found results. Downloading files to: {specific_download_dir}")
            file_results: ParfiveResults = Fido.fetch(
                query_response,
                path=str(specific_download_dir / "{file}"),
            )
            downloaded_file_paths = [str(fp) for fp in file_results.data]

            if not downloaded_file_paths:
                logger.warning("Fido reported results, but no files were actually downloaded.")
                return [], None

            logger.info(f"Download complete. {len(downloaded_file_paths)} files fetched.")
            return downloaded_file_paths, specific_download_dir
        except Exception as e:
            logger.error(
                f"Fido search/fetch failed for {target_dataset}: {e}",
                exc_info=True,
            )
            return [], None

    def _normalize_cdaweb_meta_to_variables(
        self, api_meta: List[Dict[str, Any]], file_meta: Dict[str, Any]
    ) -> List[VariableMetadata]:
        """
        Merges file and API metadata into VariableMetadata objects.

        Args:
            api_meta (List[Dict[str, Any]]): Metadata from an API source.
            file_meta (Dict[str, Any]): Metadata parsed from a data file.

        Returns:
            List[VariableMetadata]: A merged list of metadata objects.
        """
        variables: List[VariableMetadata] = []
        api_meta_dict = {var["Name"]: var for var in api_meta if var.get("Name")}
        all_var_names = set(api_meta_dict.keys()) | set(file_meta.keys())

        for var_name in all_var_names:
            if not var_name:
                continue

            api_attrs: Dict[str, Any] = api_meta_dict.get(var_name, {})
            file_attrs: Dict[str, Any] = file_meta.get(var_name, {})

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

    def _transform_dataframe_to_docs(self, df: pd.DataFrame, source_info: str) -> List[Dict[str, Any]]:
        """
        Converts a 1D TimeSeries DataFrame into a list of MongoDB documents.

        Args:
            df (pd.DataFrame): The DataFrame (from a TimeSeries) to transform.
            source_info (str): A string identifying the data source.

        Returns:
            List[Dict[str, Any]]: A list of formatted MongoDB documents.
        """
        logger.info(f"Transforming {len(df)} DataFrame rows to MongoDB documents...")

        data_payloads: List[Dict[Hashable, Any]] = df.to_dict("records")
        timestamps: np.ndarray = cast(pd.DatetimeIndex, df.index).to_pydatetime()

        metadata = {"source": source_info}

        documents = [
            {
                "timestamp": ts_py,
                "metadata": metadata,
                "data": {str(k): v for k, v in data.items() if not pd.isna(v)},
            }
            for ts_py, data in zip(timestamps, data_payloads)
        ]

        logger.info(f"Transformed {len(documents)} documents.")
        return documents

    def _parse_xarray_timestamp(self, ts_slice: xr.Dataset, time_coord: str, index: int) -> Optional[datetime]:
        """
        Safely parses the timestamp from an xarray slice.

        Args:
            ts_slice (xr.Dataset): The single time-slice of the dataset.
            time_coord (str): The name of the time coordinate.
            index (int): The current loop index (for logging).

        Returns:
            Optional[datetime]: The parsed datetime object or None if parsing fails.
        """
        try:
            time_val: Any = ts_slice[time_coord].values
            timestamp_obj: Any = Time(time_val, format="unix").to_datetime()

            if isinstance(timestamp_obj, datetime):
                return timestamp_obj
            elif hasattr(timestamp_obj, "size") and timestamp_obj.size == 1:
                timestamp_item: Any = timestamp_obj.item()
                if isinstance(timestamp_item, datetime):
                    return timestamp_item
                else:
                    logger.warning(
                        f"Timestamp at index {index} is not a scalar datetime ({type(timestamp_item)}). Skipping."
                    )
                    return None
            elif np.ma.is_masked(timestamp_obj):
                logger.warning(f"Skipping timestep {index}, time value is masked.")
                return None
            else:
                logger.warning(
                    f"Timestamp at index {index} is not a scalar datetime ({type(timestamp_obj)}). Skipping."
                )
                return None

        except Exception as e_ts:
            time_val_repr = str(ts_slice[time_coord].values)
            if hasattr(ts_slice[time_coord].values, "shape"):
                if ts_slice[time_coord].values.shape != ():
                    time_val_repr = f"Array(shape={ts_slice[time_coord].values.shape})"
            logger.warning(
                f"Skipping timestep, could not parse timestamp from "
                f"coord '{time_coord}': {time_val_repr}. Error: {e_ts}",
                exc_info=True,
            )
            return None

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

        Args:
            ds (xr.Dataset): The dataset to process.
            source_info (str): A string identifying the data source.
            time_dim (str): The name of the time dimension.
            time_coord (str): The name of the time coordinate.
            collection_name (str): The name of the target MongoDB collection.

        Yields:
            Generator[Dict[str, Any], None, None]:
            A generator of MongoDB documents (pointers to GridFS).
        """
        for i in range(len(ds[time_dim])):
            ts_slice: xr.Dataset = ds.isel({time_dim: i})

            timestamp = self._parse_xarray_timestamp(ts_slice, time_coord, i)
            if timestamp is None:
                continue

            data_payload: Dict[str, Any] = {}
            for var_name in ts_slice.data_vars:
                try:
                    var_data: xr.DataArray = ts_slice[var_name]
                    data_payload[str(var_name)] = var_data.values.tolist()
                except Exception as e_var:
                    logger.warning(f"Could not process variable '{var_name}' for timestamp {timestamp}: {e_var}")

            try:
                payload_bytes: bytes = pickle.dumps(data_payload)
            except Exception as e_pickle:
                logger.error(
                    f"Failed to serialize data payload for {timestamp}: {e_pickle}",
                    exc_info=True,
                )
                continue

            try:
                gridfs_id: ObjectId = self.fs.put(
                    payload_bytes,
                    filename=(f"{ds.attrs.get('Dataset_ID', 'data')}_{timestamp.isoformat()}.pkl"),
                    encoding="binary",
                    timestamp=timestamp,
                    metadata={"collection": collection_name},
                )
            except Exception as e_gridfs:
                logger.error(
                    f"Failed to write payload to GridFS for {timestamp}: {e_gridfs}",
                    exc_info=True,
                )
                continue

            yield {
                "timestamp": timestamp,
                "metadata": {"source": source_info},
                "data_gridfs_id": gridfs_id,
            }

    def _handle_1d_data(
        self,
        data_object: GenericTimeSeries,
        collection_name: str,
        source_id: str,
    ) -> None:
        """
        Transforms and loads 1D TimeSeries data into MongoDB.

        Args:
            data_object (GenericTimeSeries): The parsed 1D timeseries.
            collection_name (str): The target MongoDB collection name.
            source_id (str): The source identifier string
        """
        logger.info(f"1D data for {collection_name} is parsed. Transforming for MongoDB load...")
        df_to_load: pd.DataFrame = data_object.to_dataframe()
        documents: List[Dict[str, Any]] = self._transform_dataframe_to_docs(df_to_load, source_id)

        self._load_data(collection_name, documents)
        logger.info(f"Successfully loaded {len(documents)} documents into collection '{collection_name}'")

    def _load_nd_data_to_gridfs(
        self,
        data_object: List[Tuple[xr.Dataset, str, str]],
        collection_name: str,
        source_id: str,
        opened_datasets: List[xr.Dataset],
    ) -> None:
        """
        Loads N-D data by processing files sequentially and storing
        payloads in GridFS.

        Args:
            data_object (List[Tuple[xr.Dataset, str, str]]): The list of
                (dataset, time_dim, time_coord) tuples from the parser.
            collection_name (str): The name of the MongoDB collection.
            source_id (str): The source identifier string.
            opened_datasets (List[xr.Dataset]): A list to append
                opened datasets to (for later closing).
        """
        logger.info(f"N-D data for {collection_name} is parsed. Storing payloads in GridFS...")
        opened_datasets.extend([ds for ds, _, _ in data_object])

        total_loaded = 0
        n_files = len(data_object)
        logger.info(f"Starting file processing for {n_files} files (sequentially)...")

        for i, (ds_file, time_dim, time_coord) in enumerate(data_object):
            logger.info(f"--- Processing file {i + 1}/{n_files} ---")
            try:
                logger.info(f"  -> Found time dimension: '{time_dim}' (coord: '{time_coord}')")
                logger.info(f"  -> File has {len(ds_file[time_dim])} timesteps. Transforming and loading in batches...")

                documents_batch: List[Dict[str, Any]] = []
                for doc in self._transform_xarray_to_docs(
                    ds_file,
                    source_id,
                    time_dim,
                    time_coord,
                    collection_name,
                ):
                    documents_batch.append(doc)

                    if len(documents_batch) >= self.DEFAULT_BATCH_SIZE:
                        self._load_data(
                            collection_name,
                            documents_batch,
                        )
                        total_loaded += len(documents_batch)
                        logger.info(f"    -> ... batch loaded. Total docs so far: {total_loaded}")
                        documents_batch.clear()

                if documents_batch:
                    self._load_data(
                        collection_name,
                        documents_batch,
                    )
                    total_loaded += len(documents_batch)
                    logger.info(f"    -> ... final batch loaded. Total docs so far: {total_loaded}")
                    documents_batch.clear()

            except Exception as e:
                logger.error(f"Failed to process file {i + 1}: {e}", exc_info=True)

        logger.info(f"N-D data processing complete for this chunk. Documents (pointers) loaded: {total_loaded}")

    def _save_pipeline_metadata(
        self,
        dataset_id: str,
        group_name: str,
        file_metadata: Dict[str, Any],
    ) -> None:
        """
        Normalizes and saves the pipeline metadata to the database.

        Args:
            dataset_id (str): The dataset ID.
            group_name (str): The source group name (from catalog).
            file_metadata (Dict[str, Any]): The metadata parsed from the file.
        """
        variables: List[VariableMetadata] = self._normalize_cdaweb_meta_to_variables([], file_metadata)
        source_info: Dict[str, str] = {
            "Source Client": FIDO_CDAWEB_SOURCE_ID,
            "Source Group": group_name,
        }
        self.metadata_writer.save_metadata_to_db(
            dataset_id=dataset_id,
            variables=variables,
            source_info=source_info,
        )

    def _process_dataset(
        self,
        dataset_id: str,
        group_name: str,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        """
        Runs the full pipeline for a single dataset chunk.

        (Download, Parse, Transform, Load)

        Args:
            dataset_id (str): The dataset ID to process.
            group_name (str): The group name (for download path).
            start_time (datetime): The start time for the query chunk.
            end_time (datetime): The end time for the query chunk.
        """
        logger.info(f"--- Processing Dataset Chunk: {dataset_id} ({start_time} to {end_time}) ---")

        opened_datasets: List[xr.Dataset] = []
        try:
            downloaded_files, _ = self._fetch_data_for_dataset(group_name, dataset_id, start_time, end_time)

            if downloaded_files:
                data_object: Optional[Union[GenericTimeSeries, List[Tuple[xr.Dataset, str, str]]]]
                file_metadata: Dict[str, Any]
                data_object, file_metadata = parse_datafiles(
                    downloaded_files,
                    start_time,
                    end_time,
                    variables_to_parse=[],
                )

                collection_name = sanitize_collection_name(dataset_id)
                source_id = f"{FIDO_CDAWEB_SOURCE_ID}/{group_name}"

                if data_object is not None:
                    if isinstance(data_object, GenericTimeSeries):
                        self._handle_1d_data(data_object, collection_name, source_id)
                    elif isinstance(data_object, list):
                        self._load_nd_data_to_gridfs(
                            data_object,
                            collection_name,
                            source_id,
                            opened_datasets,
                        )
                    else:
                        logger.warning(
                            f"Data object for {dataset_id} was not a "
                            f"recognized type ({type(data_object)}). "
                            "Cannot load."
                        )
                else:
                    logger.info(f"No 1D or N-D data could be parsed for {dataset_id} in this chunk.")
                if file_metadata:
                    self._save_pipeline_metadata(dataset_id, group_name, file_metadata)
            else:
                logger.warning(f"Skipping parse/metadata for {dataset_id} in chunk: No files downloaded.")

        except Exception as e:
            logger.error(
                f"Pipeline failed for dataset chunk {dataset_id}: {e}",
                exc_info=True,
            )
        finally:
            for ds in opened_datasets:
                try:
                    ds.close()
                except Exception as e_close:
                    logger.warning(f"Failed to close xarray dataset: {e_close}")
            logger.info(f"--- Finished Dataset Chunk: {dataset_id} ---")

    def run(
        self,
        datasets_to_process: List[str],
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        """
        Runs the complete data pipeline orchestration.

        This method iterates through the specified time range in 7-day
        chunks to avoid failures on large requests with data gaps.
        It checks if data for a chunk already exists before fetching.

        Args:
            datasets_to_process (List[str]): A list of dataset IDs to fetch.
            start_time (datetime): The start time for the data query.
            end_time (datetime): The end time for the data query.
        """
        logger.info("=========================================")
        logger.info(f"Starting CDAWeb Pipeline (FILTERED: {datasets_to_process})")
        logger.info(f"Time Range: {start_time} to {end_time}")
        logger.info(f"Data Root: {self.base_data_dir}")
        logger.info("=========================================")

        if not datasets_to_process:
            logger.warning("No datasets specified in 'datasets_to_process'.")
            logger.info("=========================================")
            logger.info("CDAWeb Pipeline finished (no work done).")
            logger.info("=========================================")
            return

        logger.info(f"--- Will process {len(datasets_to_process)} FIDO datasets ---")

        chunk_delta = timedelta(days=7)
        time_chunks = generate_time_chunks(start_time, end_time, chunk_delta)
        time_field = "timestamp"  # The field to check for existence

        logger.info(f"Time range {start_time} to {end_time} split into {len(time_chunks)} chunk(s).")

        for dataset_id in datasets_to_process:
            group_name = self._get_fido_source_from_db(dataset_id)

            if not group_name:
                logger.error(
                    f"Skipping {dataset_id}: Could not find its {FIDO_CDAWEB_SOURCE_ID} group (source_id) in catalog."
                )
                continue

            collection_name = sanitize_collection_name(dataset_id)
            collection = self.db[collection_name]

            self._ensure_timeseries_collection(
                collection_name,
                time_field=time_field,
                meta_field="metadata",
            )

            logger.info(f"Processing dataset {dataset_id} (group {group_name}) for all {len(time_chunks)} chunks...")
            for i, (chunk_start, chunk_stop) in enumerate(time_chunks):
                logger.info(f"  -> Checking chunk {i + 1}/{len(time_chunks)}: {chunk_start} to {chunk_stop}")

                # Check if data for this chunk already exists
                query = {time_field: {"$gte": chunk_start, "$lt": chunk_stop}}
                exists = collection.find_one(query, {"_id": 1})

                if exists:
                    logger.info("Skipping chunk: Data already exists in DB.")
                    continue

                logger.info("Processing chunk: No data found in DB.")
                self._process_dataset(dataset_id, group_name, chunk_start, chunk_stop)

        logger.info("=========================================")
        logger.info("CDAWeb Pipeline finished.")
        logger.info("=========================================")
