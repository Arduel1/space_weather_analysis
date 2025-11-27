import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from hapiclient import hapi
from hapiclient.util import HAPIError
from pymongo.database import Database

from space_weather_analysis.config import HAPI_SOURCE_ID
from space_weather_analysis.util.time_utils import calculate_time_intersection, parse_iso_time

logger = logging.getLogger(__name__)


class HapiClient:
    """
    A generic client that uses a master catalog from MongoDB to find and
    stream data from a data service.

    This client abstracts the HAPI protocol details and implements server
    fallback logic using the catalog's source list. It handles metadata
    retrieval and data streaming for specified time ranges.
    """

    CATALOG_COLLECTION = "master_catalog"

    def __init__(self, db: Database):
        """
        Initializes the client using a MongoDB database connection.

        The client loads the master catalog into memory upon initialization
        to enable fast lookups of dataset sources.

        Args:
            db (Database): The MongoDB database instance.
        """
        self.db = db
        self.catalog_map = self._load_catalog_from_db()
        if self.catalog_map:
            logger.info(f"Loaded master catalog from DB with {len(self.catalog_map)} entries.")
        else:
            logger.error("Failed to load master catalog from database. Client may not function correctly.")

    def _load_catalog_from_db(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Loads the master catalog from the MongoDB collection into a dictionary map.

        The map structure allows for O(1) access to source lists by dataset_id.

        Returns:
            Dict[str, List[Dict[str, Any]]]: A dictionary mapping dataset_id
                                              to a list of source entries.
        """
        try:
            collection = self.db[self.CATALOG_COLLECTION]
            # Find all documents, excluding the internal MongoDB '_id' field
            catalog_data = collection.find({}, {"_id": 0})

            # Convert to dictionary map: {dataset_id: [sources...]}
            return {doc["dataset_id"]: doc.get("sources", []) for doc in catalog_data if "dataset_id" in doc}
        except Exception as e:
            logger.error(f"Failed to load master catalog from MongoDB: {e}", exc_info=True)
            return {}

    def get_catalog_entries(self, dataset_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Finds the list of catalog entries (servers and time ranges) for a given dataset ID.

        Args:
            dataset_id (str): The unique identifier of the dataset.

        Returns:
            Optional[List[Dict[str, Any]]]: A list of source entries dictionaries,
                                            or None if the dataset ID is unknown.
        """
        entries = self.catalog_map.get(dataset_id)
        if not entries:
            logger.error(f"Dataset '{dataset_id}' not found in master catalog map.")
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
            meta = hapi(server_url, dataset_id, logging=True)
            # Enrich metadata with provenance information
            meta["x_server_url"] = server_url
            return meta
        except HAPIError:
            # This is expected behavior during fallback attempts, so we log as info
            logger.info(f"Metadata fallback failed for {dataset_id} at {server_url}.")
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

        logger.info(f"No parameters specified for {dataset_id}. Fetching metadata first...")
        meta_info = self.get_metadata(dataset_id)

        if meta_info and "parameters" in meta_info:
            # Filter out 'Time' as it is always returned by HAPI
            params_to_use = [p["name"] for p in meta_info["parameters"] if p.get("name") != "Time"]
            logger.info(f"Found parameters: {params_to_use}")
            return ",".join(params_to_use)
        else:
            logger.warning(f"Could not retrieve metadata for {dataset_id}. Proceeding without explicit params.")
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
            return None, None

        params_str = self._get_parameters_for_request(dataset_id, parameters)

        for i, entry in enumerate(entries):
            source_id = entry.get("source_id")
            logger.info(f"Attempting source {i + 1}/{len(entries)} for {dataset_id} from: {source_id}")

            data, meta = self._attempt_server_entry(dataset_id, entry, config_start, config_end, params_str)

            if data is not None and meta is not None:
                return data, meta

        logger.error(f"All fallbacks failed for dataset '{dataset_id}'.")
        return None, None

    def _attempt_server_entry(
        self,
        dataset_id: str,
        entry: Dict[str, Any],
        config_start: datetime,
        config_end: datetime,
        params_str: str,
    ) -> Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
        """
        Helper to attempt data download from a single server entry.

        Checks for time intersection before making the request.

        Args:
            dataset_id (str): The dataset ID.
            entry (Dict[str, Any]): The catalog entry containing source_id and time_range.
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
            logger.warning("Skipping entry: catalog entry is incomplete (missing source_id or time_range).")
            return None, None

        # Parse server availability times
        server_start = parse_iso_time(time_range.get("start_time"))
        server_end = parse_iso_time(time_range.get("end_time"))

        # Calculate intersection with requested range
        valid_range = calculate_time_intersection(config_start, config_end, server_start, server_end)

        if not valid_range:
            logger.warning(
                f"Skipping server {server_url}: No time overlap. "
                f"Request: {config_start}-{config_end}, Available: {server_start}-{server_end}."
            )
            return None, None

        valid_start, valid_stop = valid_range
        logger.info(f"Requesting valid intersection: {valid_start} to {valid_stop}")

        try:
            # HAPI requires ISO strings formatted with 'Z' for UTC
            start_str = valid_start.isoformat(timespec="seconds").replace("+00:00", "Z")
            stop_str = valid_stop.isoformat(timespec="seconds").replace("+00:00", "Z")

            logger.info(
                f"{HAPI_SOURCE_ID} Request: dataset={dataset_id}, "
                f"start={start_str}, stop={stop_str}, params={params_str}"
            )

            data, meta = hapi(server_url, dataset_id, params_str, start_str, stop_str, logging=True)

            logger.info(f"Successfully fetched {dataset_id} from {server_url}")
            # Add provenance
            meta["x_server_url"] = server_url
            return data, meta

        except HAPIError as e:
            logger.error(
                f"Data request failed for {dataset_id} at {server_url}. Server responded: {e}",
                exc_info=True,
            )
            return None, None
        except Exception as e:
            logger.error(
                f"Unexpected error during data request for {dataset_id} from {server_url}: {e}",
                exc_info=True,
            )
            return None, None
