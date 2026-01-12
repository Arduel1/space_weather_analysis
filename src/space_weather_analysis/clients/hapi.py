import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, cast

import numpy as np
from hapiclient import hapi
from hapiclient.util import HAPIError
from pymongo.database import Database

from space_weather_analysis.clients.base import BaseClient
from space_weather_analysis.util.error_handling import log_and_handle_expected_failure
from space_weather_analysis.util.resilience import retry_interaction
from space_weather_analysis.util.time_utils import calculate_time_intersection, parse_iso_time
from space_weather_analysis.util.types import CatalogEntryDict

logger = logging.getLogger(__name__)


class HapiClient(BaseClient):
    """
    A generic client that uses a master catalog from MongoDB to find and
    stream data from HAPI-compliant data services.

    This client abstracts the HAPI protocol details and implements server
    fallback logic using the catalog's source list. It handles metadata
    retrieval and data streaming for specified time ranges.
    """

    CATALOG_COLLECTION = "master_catalog"
    SOURCE_TYPE = "HAPI"

    def __init__(self, db: Optional[Database] = None) -> None:
        """
        Initializes the client using a MongoDB database connection.

        Args:
            db: The MongoDB database instance.
        """
        self.db = db
        self.catalog_map: Dict[str, List[CatalogEntryDict]] = {}

        if self.db is not None:
            try:
                loaded_map = self._load_catalog_from_db()
                if loaded_map:
                    self.catalog_map = loaded_map
                    logger.info(f"Loaded master catalog from DB with {len(self.catalog_map)} entries.")
                else:
                    logger.warning("Master catalog loaded but empty. Discovery may need to run first.")
            except Exception as e:
                logger.error(f"Failed to initialize catalog map: {e}", exc_info=True)

    def _load_catalog_from_db(self) -> Dict[str, List[CatalogEntryDict]]:
        """
        Loads the master catalog from the MongoDB collection into a dictionary map.

        Returns:
            A dictionary mapping dataset_id to a list of source entries.
        """
        if self.db is None:
            return {}

        try:
            collection = self.db[self.CATALOG_COLLECTION]
            catalog_data = collection.find({}, {"_id": 0})

            result: Dict[str, List[CatalogEntryDict]] = {}
            for doc in catalog_data:
                dataset_id = doc.get("dataset_id")
                sources = doc.get("sources", [])

                if dataset_id and isinstance(dataset_id, str):
                    result[dataset_id] = cast(List[CatalogEntryDict], sources)

            return result

        except Exception as e:
            logger.error(f"Failed to load master catalog from MongoDB: {e}", exc_info=True)
            return {}

    # --- Discovery Methods ---

    @retry_interaction(max_retries=3, exceptions=(Exception,))
    def fetch_server_catalog(self, server_url: str) -> List[Dict[str, Any]]:
        """
        Fetches the full catalog from a specific HAPI server URL.

        Args:
            server_url: The base URL of the HAPI server.

        Returns:
            The list of datasets advertised by the server.
        """
        try:
            # logging=False suppresses the internal print statements of hapiclient
            response = hapi(server_url, logging=False)
            catalog = response.get("catalog", [])
            logger.info(f"Discovered {len(catalog)} datasets at {server_url}")
            return catalog
        except Exception as e:
            logger.warning(f"Failed to fetch catalog from {server_url}: {e}")
            raise

    @retry_interaction(max_retries=3, exceptions=(Exception,))
    def fetch_dataset_metadata_from_server(self, dataset_id: str, server_url: str) -> Optional[Dict[str, Any]]:
        """
        Fetches metadata for a single dataset directly from a specific server.

        Args:
            dataset_id: The dataset ID to fetch.
            server_url: The URL of the HAPI server.

        Returns:
            The metadata dictionary or None on failure.
        """
        try:
            meta = hapi(server_url, dataset_id, logging=False)

            if not isinstance(meta, dict):
                logger.warning(f"Unexpected metadata format from {server_url}: {type(meta)}")
                return None

            meta["x_server_url"] = server_url
            return meta

        except HAPIError as e:
            logger.info(f"Metadata fetch failed for {dataset_id} at {server_url}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error fetching metadata from {server_url}: {e}")
            raise

    # --- BaseClient Interface Implementation ---

    def get_catalog_entries(self) -> List[Dict[str, Any]]:
        """
        Returns all datasets known to this client (from local DB map).

        Returns:
            A list of dataset entries suitable for the generic interface.
        """
        return [{"id": k, "sources": v} for k, v in self.catalog_map.items()]

    def get_dataset_metadata(self, dataset_id: str) -> Dict[str, Any]:
        """
        Fetches the metadata (HAPI 'info') for a dataset.

        Iterates through known servers until metadata is retrieved.

        Args:
            dataset_id: The unique identifier of the dataset.

        Returns:
            The metadata dictionary or empty dict on failure.
        """
        entries = self.catalog_map.get(dataset_id)
        if not entries:
            return {}

        for entry in entries:
            server_url = entry.get("source_id")
            if not server_url:
                continue

            # Utilizing the retry-enabled method
            meta = self.fetch_dataset_metadata_from_server(dataset_id, server_url)
            if meta:
                return meta

        logger.error(f"Could not retrieve metadata for {dataset_id} from any available server.")
        return {}

    def get_data(
        self, dataset_id: str, start_time: datetime, end_time: datetime, **kwargs: Any
    ) -> Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
        """
        Fetches both data and metadata for a dataset within a time range.

        Args:
            dataset_id: The dataset ID to fetch.
            start_time: The desired start time.
            end_time: The desired end time.
            **kwargs: Can contain 'parameters' (list of strings).

        Returns:
            A tuple containing the data (NumPy array) and metadata dict.
        """
        parameters = kwargs.get("parameters")
        return self.get_data_and_meta(dataset_id, start_time, end_time, parameters)

    # --- Internal Logic ---

    def _get_parameters_for_request(self, dataset_id: str, parameters: Optional[List[str]]) -> str:
        """
        Constructs the parameter string for the HAPI request.

        Args:
            dataset_id: The dataset ID.
            parameters: A list of requested parameter names.

        Returns:
            A comma-separated string of parameters.
        """
        if parameters:
            return ",".join(parameters)

        logger.info(f"No parameters specified for {dataset_id}. Fetching metadata to auto-discover...")
        meta_info = self.get_dataset_metadata(dataset_id)

        if meta_info and "parameters" in meta_info:
            params_to_use = [p["name"] for p in meta_info["parameters"] if isinstance(p, dict) and p.get("name") != "Time"]
            if params_to_use:
                logger.info(f"Auto-discovered parameters: {params_to_use}")
                return ",".join(params_to_use)

        logger.warning(f"Could not auto-discover parameters for {dataset_id}. Requesting all.")
        return ""

    def get_data_and_meta(
        self,
        dataset_id: str,
        config_start: datetime,
        config_end: datetime,
        parameters: Optional[List[str]] = None,
    ) -> Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
        """
        Fetches both data and metadata using server fallback logic.

        Args:
            dataset_id: The dataset ID to fetch.
            config_start: The desired start time (must be aware).
            config_end: The desired end time (must be aware).
            parameters: Specific parameters to fetch.

        Returns:
            (data, meta) on success, or (None, None) on failure.
        """
        entries = self.catalog_map.get(dataset_id)
        if not entries:
            logger.error(f"Cannot fetch data: Dataset {dataset_id} has no catalog entries.")
            return None, None

        params_str = self._get_parameters_for_request(dataset_id, parameters)

        for i, entry in enumerate(entries):
            source_id = entry.get("source_id", "Unknown")
            logger.info(f"Attempting source {i + 1}/{len(entries)} for {dataset_id}: {source_id}")

            data, meta = self._attempt_server_entry(dataset_id, entry, config_start, config_end, params_str)

            if data is not None and meta is not None:
                return data, meta

        logger.error(f"All sources failed for dataset '{dataset_id}'.")
        return None, None

    @retry_interaction(max_retries=3, exceptions=(Exception,))
    def _attempt_server_entry(
        self,
        dataset_id: str,
        entry: CatalogEntryDict,
        config_start: datetime,
        config_end: datetime,
        params_str: str,
    ) -> Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
        """
        Attempts data download from a single server entry with retry logic.

        Args:
            dataset_id: The dataset ID.
            entry: The catalog entry containing source_id and time_range.
            config_start: The requested start time.
            config_end: The requested end time.
            params_str: Comma-separated parameters.

        Returns:
            (data, meta) on success, or (None, None) on failure.
        """
        server_url = entry.get("source_id")
        time_range = entry.get("time_range")

        if not server_url or not time_range:
            logger.warning("Skipping entry: incomplete metadata.")
            return None, None

        server_start = parse_iso_time(time_range["start_time"])
        server_end = parse_iso_time(time_range["end_time"])

        valid_range = calculate_time_intersection(config_start, config_end, server_start, server_end)

        if not valid_range:
            logger.debug(f"Skipping {server_url}: No overlap. Req: {config_start}-{config_end}, Avail: {server_start}-{server_end}")
            return None, None

        valid_start, valid_stop = valid_range

        # HAPI requires 'Z' for UTC time
        start_str = valid_start.isoformat(timespec="seconds").replace("+00:00", "Z")
        stop_str = valid_stop.isoformat(timespec="seconds").replace("+00:00", "Z")

        logger.info(f"{self.SOURCE_TYPE} Request: {dataset_id} [{start_str} -> {stop_str}]")

        try:
            # We call hapiclient directly here.
            # The decorator handles retries for network glitches.
            data, meta = hapi(server_url, dataset_id, params_str, start_str, stop_str, logging=False)

            if data is None or len(data) == 0:
                logger.warning(f"Server {server_url} returned no data for {dataset_id}.")
                return None, None

            logger.info(f"Successfully fetched {len(data)} records from {server_url}")
            meta["x_server_url"] = server_url
            return data, meta

        except HAPIError as e:
            # HAPI Errors (like 404s or Bad Request) usually shouldn't be retried indefinitely
            # but usually indicate permanent issues with the query for that server.
            # We treat them as expected failures for this specific mirror.
            log_and_handle_expected_failure(f"HAPI request for {dataset_id}", f"server {server_url}", e, logger)
            return None, None

        except Exception as e:
            # Unexpected exceptions are re-raised so the retry decorator catches them
            logger.warning(f"Transient error fetching from {server_url}: {e}")
            raise
