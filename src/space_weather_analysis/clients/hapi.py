import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, cast

import numpy as np
from hapiclient import hapi
from hapiclient.util import HAPIError
from pymongo.database import Database

from common.util.time_utils import calculate_time_intersection, parse_iso_time
from common.util.types import CatalogEntryDict
from space_weather_analysis.config import HAPI_SOURCE_ID
from space_weather_analysis.util.error_handling import (
    log_and_handle_expected_failure,
    log_and_handle_unexpected_failure,
)

logger = logging.getLogger(__name__)


class HapiClient:
    """
    A generic client that uses a master catalog from MongoDB to find and
    stream data from HAPI-compliant data services.

    This client abstracts the HAPI protocol details and implements server
    fallback logic using the catalog's source list. It handles metadata
    retrieval and data streaming for specified time ranges.
    """

    CATALOG_COLLECTION = "master_catalog"

    def __init__(self, db: Database) -> None:
        """
        Initializes the client using a MongoDB database connection.

        The client attempts to load the master catalog into memory upon initialization
        to enable fast lookups of dataset sources. If the DB is empty (e.g. after
        restart), it starts with an empty map but remains functional.

        Args:
            db (Database): The MongoDB database instance.
        """
        self.db = db
        # Initialize with empty map to ensure type safety even if load fails
        self.catalog_map: Dict[str, List[CatalogEntryDict]] = {}

        try:
            loaded_map = self._load_catalog_from_db()
            if loaded_map:
                self.catalog_map = loaded_map
                logger.info(f"Loaded master catalog from DB with {len(self.catalog_map)} entries.")
            else:
                logger.warning("Master catalog loaded but empty. Discovery may need to run first.")
        except Exception as e:
            # We catch generic exceptions here to prevent the application from crashing
            # during initialization if the DB is unreachable or malformed.
            logger.error(f"Failed to initialize catalog map: {e}", exc_info=True)

    def _load_catalog_from_db(self) -> Dict[str, List[CatalogEntryDict]]:
        """
        Loads the master catalog from the MongoDB collection into a dictionary map.

        The map structure allows for O(1) access to source lists by dataset_id.

        Returns:
            Dict[str, List[CatalogEntryDict]]: A dictionary mapping dataset_id
                                               to a list of source entries.
        """
        try:
            collection = self.db[self.CATALOG_COLLECTION]
            # Find all documents, excluding the internal MongoDB '_id' field
            catalog_data = collection.find({}, {"_id": 0})

            result: Dict[str, List[CatalogEntryDict]] = {}
            for doc in catalog_data:
                dataset_id = doc.get("dataset_id")
                sources = doc.get("sources", [])

                if dataset_id and isinstance(dataset_id, str):
                    # We cast here because we trust the ETL process put valid dicts in DB,
                    # but strictly speaking runtime validation (Pydantic) would be better.
                    # For now, TypedDict cast is sufficient for static analysis.
                    result[dataset_id] = cast(List[CatalogEntryDict], sources)

            return result

        except Exception as e:
            logger.error(f"Failed to load master catalog from MongoDB: {e}", exc_info=True)
            return {}

    def get_catalog_entries(self, dataset_id: str) -> Optional[List[CatalogEntryDict]]:
        """
        Finds the list of catalog entries (servers and time ranges) for a given dataset ID.

        Args:
            dataset_id (str): The unique identifier of the dataset.

        Returns:
            Optional[List[CatalogEntryDict]]: A list of source entries,
                                              or None if the dataset ID is unknown.
        """
        entries = self.catalog_map.get(dataset_id)
        if not entries:
            # Log as debug/info to avoid flooding logs if many datasets are missing
            logger.info(f"Dataset '{dataset_id}' not found in local catalog map.")
            return None
        return entries

    def get_metadata(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetches the metadata (HAPI 'info') for a dataset.

        This method iterates through all available servers for the dataset
        until one successfully returns the metadata.

        Args:
            dataset_id (str): The unique identifier of the dataset.

        Returns:
            Optional[Dict[str, Any]]: The metadata dictionary returned by the HAPI server,
                                      or None if retrieval failed from all sources.
        """
        entries = self.get_catalog_entries(dataset_id)
        if not entries:
            return None

        for entry in entries:
            server_url = entry.get("source_id")
            if not server_url:
                continue

            meta = self._attempt_metadata_from_server(dataset_id, server_url)
            if meta:
                return meta  # Return immediately on first success

        logger.error(f"Could not retrieve metadata for {dataset_id} from any available server.")
        return None

    def _attempt_metadata_from_server(self, dataset_id: str, server_url: str) -> Optional[Dict[str, Any]]:
        """
        Helper method to attempt fetching metadata from a single server.

        Args:
            dataset_id (str): The dataset ID to fetch.
            server_url (str): The URL of the HAPI server.

        Returns:
            Optional[Dict[str, Any]]: The metadata dictionary or None on failure.
        """
        try:
            # hapi() returns (data, meta). When fetching info only, data is usually None or ignored.
            # We explicitly request only parameters='' to get info if supported,
            # but hapi(server, dataset) implies fetching info/catalog depending on args.
            # The python hapiclient syntax: hapi(server, dataset) gets info.
            meta = hapi(server_url, dataset_id, logging=False)

            # Ensure we have a dict
            if not isinstance(meta, dict):
                logger.warning(f"Unexpected metadata format from {server_url}: {type(meta)}")
                return None

            # Enrich metadata with provenance information
            meta["x_server_url"] = server_url
            return meta

        except HAPIError as e:
            # This is expected behavior during fallback attempts
            logger.info(f"Metadata fetch failed for {dataset_id} at {server_url}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error fetching metadata from {server_url}: {e}")
            return None

    def _get_parameters_for_request(self, dataset_id: str, parameters: Optional[List[str]]) -> str:
        """
        Constructs the parameter string for the HAPI request.

        If specific parameters are not provided, this method fetches the dataset metadata
        to determine all available parameters (excluding the implicit 'Time' parameter).

        Args:
            dataset_id (str): The dataset ID.
            parameters (Optional[List[str]]): A list of requested parameter names.

        Returns:
            str: A comma-separated string of parameters.
        """
        if parameters:
            return ",".join(parameters)

        logger.info(f"No parameters specified for {dataset_id}. Fetching metadata to auto-discover...")
        meta_info = self.get_metadata(dataset_id)

        if meta_info and "parameters" in meta_info:
            # Filter out 'Time' as it is always returned by HAPI
            params_to_use = [p["name"] for p in meta_info["parameters"] if isinstance(p, dict) and p.get("name") != "Time"]
            if params_to_use:
                logger.info(f"Auto-discovered parameters: {params_to_use}")
                return ",".join(params_to_use)

        logger.warning(f"Could not auto-discover parameters for {dataset_id}. Requesting all (empty string).")
        return ""

    def get_data_and_meta(
        self,
        dataset_id: str,
        config_start: datetime,
        config_end: datetime,
        parameters: Optional[List[str]] = None,
    ) -> Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
        """
        Fetches both data and metadata for a dataset within a time range.

        Implements fallback logic: Iterates through all known servers for the dataset.
        For each server, it calculates the intersection between the requested time range
        and the server's available time range. It attempts to download data from the
        first server that offers a valid intersection.

        Args:
            dataset_id (str): The dataset ID to fetch.
            config_start (datetime): The desired start time (must be aware).
            config_end (datetime): The desired end time (must be aware).
            parameters (Optional[List[str]]): Specific parameters to fetch.

        Returns:
            Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
                A tuple containing:
                - The data as a NumPy structured array (or None on failure).
                - The metadata dictionary (or None on failure).
        """
        entries = self.get_catalog_entries(dataset_id)
        if not entries:
            logger.error(f"Cannot fetch data: Dataset {dataset_id} has no catalog entries.")
            return None, None

        # Resolve parameters string once
        params_str = self._get_parameters_for_request(dataset_id, parameters)

        for i, entry in enumerate(entries):
            source_id = entry.get("source_id", "Unknown")
            logger.info(f"Attempting source {i + 1}/{len(entries)} for {dataset_id}: {source_id}")

            data, meta = self._attempt_server_entry(dataset_id, entry, config_start, config_end, params_str)

            if data is not None and meta is not None:
                return data, meta

        logger.error(f"All sources failed for dataset '{dataset_id}'.")
        return None, None

    def _attempt_server_entry(
        self,
        dataset_id: str,
        entry: CatalogEntryDict,
        config_start: datetime,
        config_end: datetime,
        params_str: str,
    ) -> Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
        """
        Attempts data download from a single server entry with standardized error handling.

        This method checks for time intersection before making the request.

        Args:
            dataset_id (str): The dataset ID.
            entry (CatalogEntryDict): The catalog entry containing source_id and time_range.
            config_start (datetime): The requested start time.
            config_end (datetime): The requested end time.
            params_str (str): Comma-separated parameters.

        Returns:
            Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
                (data, meta) on success, or (None, None) on failure.
        """
        server_url = entry.get("source_id")
        time_range = entry.get("time_range")

        if not server_url or not time_range:
            logger.warning("Skipping entry: incomplete metadata (missing source_id or time_range).")
            return None, None

        # Parse times securely from the catalog
        server_start = parse_iso_time(time_range["start_time"])
        server_end = parse_iso_time(time_range["end_time"])

        valid_range = calculate_time_intersection(config_start, config_end, server_start, server_end)

        if not valid_range:
            logger.debug(f"Skipping {server_url}: No overlap.Req: {config_start}-{config_end}, Avail: {server_start}-{server_end}")
            return None, None

        valid_start, valid_stop = valid_range

        # HAPI requires 'Z' for UTC time
        start_str = valid_start.isoformat(timespec="seconds").replace("+00:00", "Z")
        stop_str = valid_stop.isoformat(timespec="seconds").replace("+00:00", "Z")

        logger.info(f"{HAPI_SOURCE_ID} Request: {dataset_id} [{start_str} -> {stop_str}] params='{params_str}'")

        try:
            # Logging=False to reduce noise in console, we handle logging here
            data, meta = hapi(server_url, dataset_id, params_str, start_str, stop_str, logging=False)

            if data is None or len(data) == 0:
                logger.warning(f"Server {server_url} returned no data for {dataset_id}.")
                return None, None

            logger.info(f"Successfully fetched {len(data)} records from {server_url}")
            meta["x_server_url"] = server_url
            return data, meta

        except HAPIError as e:
            log_and_handle_expected_failure(f"HAPI request for {dataset_id}", f"server {server_url}", e, logger)
            return None, None

        except Exception as e:
            log_and_handle_unexpected_failure(f"HAPI request for {dataset_id}", f"server {server_url}", e, logger)
            return None, None
