import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from hapiclient import hapi
from hapiclient.util import HAPIError
from pymongo.database import Database

logger = logging.getLogger(__name__)


def _parse_hapi_time(time_str: str) -> datetime:
    """
    Safely parses an ISO time string.

    Ensures the resulting datetime is always timezone-aware (UTC).

    Args:
        time_str (str): The ISO time string.

    Returns:
        datetime: A timezone-aware datetime object. Returns datetime.min
                  (UTC) if parsing fails.
    """
    try:
        # Ensure 'Z' (Zulu time) is correctly interpreted as +00:00
        if time_str.endswith("Z"):
            time_str = time_str[:-1] + "+00:00"

        dt = datetime.fromisoformat(time_str)

        # If no timezone info is present, assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt
    except Exception:
        logger.error(f"Failed to parse time string: {time_str}", exc_info=True)
        return datetime.min.replace(tzinfo=timezone.utc)


def _calculate_time_intersection(
    config_start: datetime,
    config_stop: datetime,
    server_start: datetime,
    server_stop: datetime,
) -> Optional[Tuple[datetime, datetime]]:
    """
    Calculates the overlapping time range (intersection).

    Requires all inputs to be timezone-aware datetime objects.

    Args:
        config_start (datetime): Desired start time (aware).
        config_stop (datetime): Desired stop time (aware).
        server_start (datetime): Server available start time (aware).
        server_stop (datetime): Server available stop time (aware).

    Returns:
        Optional[Tuple[datetime, datetime]]: The (start, stop) tuple of the
                                             overlapping period, or None
                                             if no overlap exists.
    """
    # Find the latest start time and earliest stop time
    valid_start = max(config_start, server_start)
    valid_stop = min(config_stop, server_stop)

    # If the start is before the stop, there is an overlap
    if valid_start < valid_stop:
        return (valid_start, valid_stop)
    else:
        return None


class GenericHapiClient:
    """
    A generic client that uses a master catalog from MongoDB to find and
    stream data from a data service. Implements server fallback logic.
    """

    CATALOG_COLLECTION = "master_catalog"

    def __init__(self, db: Database):
        """
        Initializes the client using a MongoDB database connection.

        Args:
            db (Database): The MongoDB database instance.
        """
        self.db = db
        self.catalog_map = self._load_catalog_from_db()
        if self.catalog_map:
            logger.info(f"Loaded master catalog from DB with {len(self.catalog_map)} entries.")
        else:
            logger.error("Failed to load master catalog from database. Client may not function.")

    def _load_catalog_from_db(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Loads the master catalog from the MongoDB collection into a
        dictionary (map) for fast lookups.

        Returns:
            Dict[str, List[Dict[str, Any]]]: A dictionary mapping dataset_id
                                              to a list of source entries.
        """
        try:
            collection = self.db[self.CATALOG_COLLECTION]
            # Find all documents, exclude the MongoDB '_id' field
            catalog_data = collection.find({}, {"_id": 0})

            # Convert the list of documents back into the dictionary map
            # {dataset_id: [sources...]} format
            catalog_map = {doc["dataset_id"]: doc.get("sources", []) for doc in catalog_data if "dataset_id" in doc}
            return catalog_map

        except Exception as e:
            logger.error(
                f"Failed to load master catalog from MongoDB: {e}",
                exc_info=True,
            )
            return {}

    def get_catalog_entries(self, dataset_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Finds the list of catalog entries (servers and time ranges)
        for a given dataset ID from the loaded catalog map.

        Args:
            dataset_id (str): The dataset ID to find.

        Returns:
            Optional[List[Dict[str, Any]]]: A list of server entries, or None.
        """
        entries = self.catalog_map.get(dataset_id)
        if not entries:
            logger.error(f"Dataset '{dataset_id}' not found in master catalog map.")
        return entries

    def _get_parameters_for_request(self, dataset_id: str, parameters: Optional[List[str]]) -> str:
        """
        Determines the parameters string for the data request.

        If no parameters are provided, it attempts to fetch the metadata
        first to get a complete list.

        Args:
            dataset_id (str): The dataset ID to fetch.
            parameters (Optional[List[str]]): The user-specified parameters.

        Returns:
            str: A comma-separated string of parameters, or an empty string.
        """
        if parameters:
            return ",".join(parameters)

        # If no parameters are specified, fetch metadata to get the list
        logger.info(f"No parameters specified for {dataset_id}. Fetching metadata first...")
        meta_info = self.get_metadata(dataset_id)
        if meta_info and "parameters" in meta_info:
            # Exclude the 'Time' parameter as it's implicitly returned
            params_to_use = [p["name"] for p in meta_info["parameters"] if p.get("name") != "Time"]
            logger.info(f"Found parameters: {params_to_use}")
            return ",".join(params_to_use)
        else:
            logger.warning(f"Could not retrieve metadata for {dataset_id}. Proceeding without explicit params.")
            return ""

    def _attempt_server_entry(
        self,
        dataset_id: str,
        entry: Dict[str, Any],
        config_start: datetime,
        config_stop: datetime,
        params_str: str,
    ) -> Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
        """
        Attempts to download data from a single server entry.

        This method checks time intersection and handles API request errors.

        Args:
            dataset_id (str): The dataset ID to fetch.
            entry (Dict[str, Any]): The catalog entry for a single server.
            config_start (datetime): The desired start time.
            config_stop (datetime): The desired stop time.
            params_str (str): The comma-separated parameter string.

        Returns:
            Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
            (data, meta) on success, or (None, None) on failure.
        """
        server_url = entry.get("source_id")
        time_range = entry.get("time_range")

        if not server_url or not time_range:
            logger.warning("Skipping entry: catalog entry is incomplete.")
            return None, None

        # Check Time Intersection
        server_start = _parse_hapi_time(time_range.get("start_time"))
        server_stop = _parse_hapi_time(time_range.get("end_time"))
        valid_range = _calculate_time_intersection(config_start, config_stop, server_start, server_stop)

        if not valid_range:
            logger.warning(
                f"Skipping server {server_url}: No time overlap. Available: {server_start} to {server_stop}."
            )
            return None, None

        valid_start, valid_stop = valid_range
        logger.info(f"Requesting valid intersection: {valid_start} to {valid_stop}")

        # Attempt Data Download
        try:
            start_str = valid_start.isoformat(timespec="seconds").replace("+00:00", "Z")
            stop_str = valid_stop.isoformat(timespec="seconds").replace("+00:00", "Z")

            logger.info(f"HAPI Request: dataset={dataset_id}, start={start_str}, stop={stop_str}, params={params_str}")

            data, meta = hapi(server_url, dataset_id, params_str, start_str, stop_str, logging=True)

            logger.info(f"Successfully fetched {dataset_id} from {server_url}")
            # Add provenance to metadata
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

    def get_data_and_meta(
        self,
        dataset_id: str,
        config_start: datetime,
        config_stop: datetime,
        parameters: Optional[List[str]] = None,
    ) -> Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
        """
        Fetches both data and metadata from the correct server.

        Implements fallback logic: It iterates through all known servers
        for the dataset, checks for time overlap, and tries to download
        until one succeeds.

        Args:
            dataset_id (str): The dataset ID to fetch.
            config_start (datetime): The desired start time (must be aware).
            config_stop (datetime): The desired end time (must be aware).
            parameters (Optional[List[str]]): Specific parameters to fetch.

        Returns:
            A tuple of (data, metadata) or (None, None) if all
            fallbacks fail.
        """
        entries = self.get_catalog_entries(dataset_id)
        if not entries:
            return None, None

        params_str = self._get_parameters_for_request(dataset_id, parameters)

        for i, entry in enumerate(entries):
            logger.info(f"Attempting source {i + 1}/{len(entries)} for {dataset_id} from: {entry.get('source_id')}")

            data, meta = self._attempt_server_entry(dataset_id, entry, config_start, config_stop, params_str)

            # If successful, return immediately
            if data is not None and meta is not None:
                return data, meta

        logger.error(f"All fallbacks failed for dataset '{dataset_id}'.")
        return None, None

    def _attempt_metadata_from_server(self, dataset_id: str, server_url: str) -> Optional[Dict[str, Any]]:
        """
        Attempts to fetch metadata from a single server.

        Args:
            dataset_id (str): The dataset ID to fetch.
            server_url (str): The server URL to try.

        Returns:
            Optional[Dict[str, Any]]: The metadata dictionary or None.
        """
        try:
            meta = hapi(server_url, dataset_id, logging=True)
            # Add provenance to metadata
            meta["x_server_url"] = server_url
            return meta
        except HAPIError:
            # This is expected during fallback, so log as info
            logger.info(f"Metadata fallback failed for {dataset_id} at {server_url}.")
            return None

    def get_metadata(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetches only the metadata (info) for a dataset.

        Tries all available fallback servers until one succeeds.

        Args:
            dataset_id (str): The dataset ID.

        Returns:
            Optional[Dict[str, Any]]: The metadata dictionary or None.
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
                return meta  # Return on first success

        logger.error(f"Could not retrieve metadata for {dataset_id} from any server.")
        return None
