import logging
import pathlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Generator
import pickle  # Import pickle for serialization
import gridfs  # Import GridFS

import dask.dataframe as dd
import pandas as pd
import sunpy.timeseries
import xarray as xr
from parfive.results import Results as ParfiveResults
from pymongo.database import Database
from sunpy.net import Fido
from sunpy.net import attrs as a
from sunpy.net.cdaweb import attrs as cdaweb_attrs

# CORRECTED IMPORT: Import the base class for the type check
from sunpy.timeseries.timeseriesbase import GenericTimeSeries

# --- ASTROPY IMPORT ---
from astropy.time import Time

# from space_weather_analysis.database.connection import DatabaseConnection
from space_weather_analysis.database.mongo_loader_base import MongoLoaderBase
from space_weather_analysis.sunpy_pipeline.cdaweb_client import CdawebClient

# MODIFIED IMPORT: Import the helper function
from space_weather_analysis.util.file_parser import parse_datafiles
from space_weather_analysis.util.metadata.metadata_generator import MetadataWriter
from space_weather_analysis.util.metadata.variable_metadata import VariableMetadata

logger = logging.getLogger(__name__)


class CdawebPipeline(MongoLoaderBase):
    """
    Orchestrates the end-to-end process of fetching, downloading, parsing,
    and documenting data from the CDAWeb service using Fido.
    This pipeline relies on the MongoDB master_catalog to find data.
    """

    CATALOG_COLLECTION = "master_catalog"

    def __init__(self, db: Database, base_data_path: pathlib.Path):
        """
        Initializes the pipeline.
        """
        super().__init__(db)
        self.client = CdawebClient()
        self.base_data_dir = base_data_path
        self.metadata_writer = MetadataWriter(db)

        # --- ADD GridFS ---
        self.fs = gridfs.GridFS(self.db)
        # --- END ADD ---

        if not self.base_data_dir.exists():
            self.base_data_dir.mkdir(parents=True)
            logger.info(f"Created base data directory: {self.base_data_dir}")
        logger.info(f"Pipeline initialized. Data root: {self.base_data_dir}")

    def _get_fido_source_from_db(self, dataset_id: str) -> Optional[str]:
        """
        Queries the master_catalog to find the 'FIDO_CDAWEB' source_id
        (which is the group_name) for a given dataset.
        """
        logger.info(f"Querying catalog for FIDO_CDAWEB source for: {dataset_id}")
        try:
            collection = self.db[self.CATALOG_COLLECTION]
            doc = collection.find_one({"dataset_id": dataset_id})

            if not doc:
                logger.error(f"Dataset '{dataset_id}' not found in {self.CATALOG_COLLECTION}")
                return None

            for source in doc.get("sources", []):
                if source.get("type") == "FIDO_CDAWEB":
                    source_id = source.get("source_id")  # This is the group_name
                    logger.info(f"Found FIDO source for {dataset_id}: {source_id}")
                    return source_id

            logger.warning(f"No 'FIDO_CDAWEB' type source found for {dataset_id}")
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
        Searches Fido for a dataset within a time range and downloads
        the files.
        """
        logger.info(f"Searching Fido for '{target_dataset}'...")

        try:
            specific_download_dir = self.base_data_dir / group_name / target_dataset
            time_query = a.Time(start_time, end_time)
            dataset_query = cdaweb_attrs.Dataset(target_dataset)
            query_response = Fido.search(time_query, dataset_query)

            if not query_response:
                logger.warning(f"Fido.search returned 0 results for '{target_dataset}'.")
                return [], None

            logger.info(f"Fido found results. Downloading files to: {specific_download_dir}")
            file_results: ParfiveResults = Fido.fetch(query_response, path=str(specific_download_dir / "{file}"))
            downloaded_file_paths = [str(fp) for fp in file_results.data]

            if not downloaded_file_paths:
                logger.warning("Fido reported results, but no files were actually downloaded.")
                return [], None

            logger.info(f"Download complete. {len(downloaded_file_paths)} files fetched.")
            return downloaded_file_paths, specific_download_dir
        except Exception as e:
            logger.error(f"Fido search/fetch failed for {target_dataset}: {e}", exc_info=True)
            return [], None

    def _normalize_cdaweb_meta_to_variables(
        self, api_meta: List[Dict[str, Any]], file_meta: Dict[str, Any]
    ) -> List[VariableMetadata]:
        """
        Converts file metadata from file_parser into a single list
        of standardized VariableMetadata objects.
        """
        variables = []
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

    def _transform_dataframe_to_docs(self, df: pd.DataFrame, source_info: str) -> List[Dict[str, Any]]:
        """
        Converts a Pandas DataFrame into a list of MongoDB documents.
        (Used for 1D data)
        """
        logger.info(f"Transforming {len(df)} DataFrame rows to MongoDB documents...")
        documents = []

        # This function now correctly assumes the index is a timestamp
        for timestamp, row in df.iterrows():
            if not isinstance(timestamp, pd.Timestamp):
                try:
                    # This fallback is still useful
                    timestamp = pd.Timestamp(timestamp)
                except Exception:
                    logger.warning(f"Skipping row with non-timestamp index: {timestamp}")
                    continue

            data_payload = {k: v for k, v in row.items() if not pd.isna(v)}
            ts_py: datetime = timestamp.to_pydatetime()

            documents.append(
                {
                    "timestamp": ts_py,
                    "metadata": {"source": source_info},
                    "data": data_payload,
                }
            )
        logger.info(f"Transformed {len(documents)} documents.")
        return documents

    # --- MODIFIED METHOD FOR GRIDFS ---
    def _transform_xarray_to_docs(
        self, ds: xr.Dataset, source_info: str, time_dim: str, time_coord: str
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Generator to convert an xarray.Dataset into MongoDB documents.
        Large N-D data payloads are stored in GridFS.
        """
        # Iterate over each timestep index
        for i in range(len(ds[time_dim])):
            # Select the slice for this timestep using the time DIMENSION
            ts_slice = ds.isel({time_dim: i})

            # Convert the timestamp to a Python datetime using the time COORDINATE
            try:
                time_val = ts_slice[time_coord].values
                # Use astropy.time.Time to parse the float (assuming unix seconds)
                timestamp = Time(time_val, format="unix").to_datetime()
            except Exception as e_ts:
                time_val_repr = str(time_val)
                if hasattr(time_val, "shape") and time_val.shape != ():
                    time_val_repr = f"Array(shape={time_val.shape})"
                logger.warning(
                    f"Skipping timestep, could not parse timestamp from coord '{time_coord}': {time_val_repr}. Error: {e_ts}",
                    exc_info=True,
                )
                continue

            data_payload = {}
            # Iterate over all data variables in the slice
            for var_name in ts_slice.data_vars:
                try:
                    var_data = ts_slice[var_name]
                    # Convert the xarray.DataArray (N-D) to a nested Python list
                    data_payload[var_name] = var_data.values.tolist()
                except Exception as e_var:
                    logger.warning(f"Could not process variable '{var_name}' for timestamp {timestamp}: {e_var}")

            # --- START GRIDFS LOGIC ---
            try:
                # Serialize the entire data payload (which is > 16MB)
                payload_bytes = pickle.dumps(data_payload)
            except Exception as e_pickle:
                logger.error(f"Failed to serialize data payload for {timestamp}: {e_pickle}", exc_info=True)
                continue

            try:
                # Store the serialized bytes in GridFS
                gridfs_id = self.fs.put(
                    payload_bytes,
                    filename=f"{ds.attrs.get('Dataset_ID', 'data')}_{timestamp.isoformat()}.pkl",
                    encoding="binary",
                    timestamp=timestamp,
                )
            except Exception as e_gridfs:
                logger.error(f"Failed to write payload to GridFS for {timestamp}: {e_gridfs}", exc_info=True)
                continue

            # Yield the *small* document containing the GridFS reference
            yield {
                "timestamp": timestamp,
                "metadata": {"source": source_info},
                "data_gridfs_id": gridfs_id,  # Pointer to the data
            }
            # --- END GRIDFS LOGIC ---

    def _sanitize_collection_name(self, dataset_id: str) -> str:
        """
        Replaces characters that are invalid in MongoDB collection names.
        """
        return dataset_id.replace("/", "_").replace("-", "_")

    def _process_dataset(
        self,
        dataset_id: str,
        group_name: str,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        """
        Runs the full pipeline (download, parse, docgen, load)
        for a single dataset.
        """
        logger.info(f"--- Processing Dataset: {dataset_id} ---")

        opened_datasets: List[xr.Dataset] = []

        try:
            downloaded_files, specific_download_dir = self._fetch_data_for_dataset(
                group_name, dataset_id, start_time, end_time
            )

            if downloaded_files and specific_download_dir:
                variables_to_parse = []  # Let file_parser find all

                data_object, file_metadata = parse_datafiles(
                    downloaded_files,
                    start_time,
                    end_time,
                    variables_to_parse,
                )

                # --- Data Loading Logic ---
                if data_object is not None:
                    collection_name = self._sanitize_collection_name(dataset_id)
                    # This is now a collection of pointers, but still time-series based
                    self._ensure_timeseries_collection(collection_name, time_field="timestamp", meta_field="metadata")
                    source_id = f"FIDO_CDAWEB/{group_name}"

                    if isinstance(data_object, GenericTimeSeries):
                        # 1D Data Path (Load all at once)
                        logger.info(f"1D data for {dataset_id} is parsed. Transforming for MongoDB load...")
                        df_to_load = data_object.to_dataframe()
                        documents = self._transform_dataframe_to_docs(df_to_load, source_id)
                        self._load_data(collection_name, documents, clear_collection=True)
                        logger.info(
                            f"Successfully loaded {len(documents)} documents into collection '{collection_name}'"
                        )

                    # MODIFIED: Handle N-D data as a list of Tuples
                    elif isinstance(data_object, list):
                        # N-D Data Path (Batch Load with GridFS)
                        # data_object is a List[Tuple[xr.Dataset, str, str]]
                        logger.info(f"N-D data for {dataset_id} is parsed. Storing payloads in GridFS...")

                        opened_datasets.extend([ds for ds, _, _ in data_object])

                        # Clear collection ONCE before processing
                        logger.info(f"Clearing existing data in {collection_name} for batch load...")
                        # --- ALSO CLEAR OLD GRIDFS FILES ---
                        # Delete old files associated with this collection
                        old_files = self.db["fs.files"].find({"metadata.collection": collection_name})
                        old_ids = [f["_id"] for f in old_files]
                        if old_ids:
                            logger.info(f"Deleting {len(old_ids)} old GridFS chunks from 'fs.chunks'...")
                            self.db["fs.chunks"].delete_many({"files_id": {"$in": old_ids}})
                            self.db["fs.files"].delete_many({"_id": {"$in": old_ids}})
                            logger.info("Old GridFS files deleted.")

                        self.db[collection_name].delete_many({})
                        # --- END CLEAR ---

                        total_loaded = 0
                        n_files = len(data_object)
                        logger.info(f"Starting xarray file processing for {n_files} files (sequentially)...")

                        # Loop 1: Iterate over each file tuple
                        for i, (ds_file, time_dim, time_coord) in enumerate(data_object):
                            logger.info(f"--- Processing file {i + 1}/{n_files} ---")

                            try:
                                # We already have the dim and coord names
                                logger.info(f"  -> Found time dimension: '{time_dim}' (coord: '{time_coord}')")
                                logger.info(
                                    f"  -> File has {len(ds_file[time_dim])} timesteps. Transforming and loading in batches..."
                                )

                                documents_batch = []
                                # Loop 2: Iterate over the generator (now yielding small docs)
                                for doc in self._transform_xarray_to_docs(ds_file, source_id, time_dim, time_coord):
                                    documents_batch.append(doc)

                                    # Check if batch is full
                                    if len(documents_batch) >= self.DEFAULT_BATCH_SIZE:
                                        self._load_data(
                                            collection_name,
                                            documents_batch,
                                            clear_collection=False,  # IMPORTANT
                                        )
                                        total_loaded += len(documents_batch)
                                        logger.info(f"    -> ... batch loaded. Total docs so far: {total_loaded}")
                                        documents_batch.clear()

                                # Load any remaining documents in the last batch
                                if documents_batch:
                                    self._load_data(
                                        collection_name,
                                        documents_batch,
                                        clear_collection=False,  # IMPORTANT
                                    )
                                    total_loaded += len(documents_batch)
                                    logger.info(f"    -> ... final batch loaded. Total docs so far: {total_loaded}")
                                    documents_batch.clear()

                            except Exception as e:
                                # Log error for this specific file and continue
                                logger.error(f"Failed to process file {i + 1}: {e}", exc_info=True)

                        logger.info(f"N-D data processing complete. Total documents (pointers) loaded: {total_loaded}")
                        # --- END: MODIFIED N-D PROCESSING ---

                    else:
                        logger.warning(
                            f"Data object for {dataset_id} was not a recognized type "
                            f"({type(data_object)}). Cannot load."
                        )
                    # --- END REFACTORED LOGIC ---

                else:
                    logger.info(f"No 1D or N-D data could be parsed for {dataset_id}. No data to load.")
                # --- End Data Loading ---

                # --- Metadata Saving (Schema) ---
                variables = self._normalize_cdaweb_meta_to_variables([], file_metadata)
                source_info = {
                    "Source Client": "FIDO_CDAWEB",
                    "Source Group": group_name,
                }
                self.metadata_writer.save_metadata_to_db(
                    dataset_id=dataset_id, variables=variables, source_info=source_info
                )
                # --- End Metadata Saving ---

            else:
                logger.warning(f"Skipping parse/metadata for {dataset_id}: No files downloaded.")

        except Exception as e:
            logger.error(f"Pipeline failed for dataset {dataset_id}: {e}", exc_info=True)
        finally:
            # --- IMPORTANT: Close all xarray.Dataset files ---
            for ds in opened_datasets:
                try:
                    ds.close()
                except Exception as e_close:
                    logger.warning(f"Failed to close xarray dataset: {e_close}")

            logger.info(f"--- Finished Dataset: {dataset_id} ---")

    def run(
        self,
        datasets_to_process: List[str],
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        """
        Runs the complete data pipeline orchestration.
        """
        logger.info("=========================================")
        logger.info(f"Starting CDAWeb Pipeline (FILTERED: {datasets_to_process})")
        logger.info(f"Time Range: {start_time} to {end_time}")
        logger.info(f"Data Root: {self.base_data_dir}")
        logger.info("=========================================")

        logger.info(f"--- Will process {len(datasets_to_process)} FIDO datasets ---")

        for dataset_id in datasets_to_process:
            group_name = self._get_fido_source_from_db(dataset_id)

            if not group_name:
                logger.error(f"Skipping {dataset_id}: Could not find its 'FIDO_CDAWEB' group (source_id) in catalog.")
                continue

            self._process_dataset(dataset_id, group_name, start_time, end_time)

        logger.info("=========================================")
        logger.info("CDAWeb Pipeline finished.")
        logger.info("=========================================")
