import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from hapiclient import hapi
from hapiclient.util import HAPIError

logger = logging.getLogger(__name__)


def _parse_hapi_time(time_str: str) -> datetime:
    """
    Safely parses HAPI ISO time strings.

    Ensures the resulting datetime is always timezone-aware (UTC).

    Args:
        time_str (str): The HAPI ISO time string.

    Returns:
        datetime: A timezone-aware datetime object. Returns datetime.min
                  in UTC if parsing fails.
    """
    try:
        if time_str.endswith("Z"):
            time_str = time_str[:-1] + "+00:00"

        dt = datetime.fromisoformat(time_str)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt
    except Exception:
        logger.error(f"Failed to parse HAPI time string: {time_str}", exc_info=True)
        return datetime.min.replace(tzinfo=timezone.utc)


def _calculate_time_intersection(
    config_start: datetime,
    config_stop: datetime,
    server_start: datetime,
    server_stop: datetime,
) -> Optional[Tuple[datetime, datetime]]:
    """
    Calculates the overlapping time range (intersection).

    Requires all inputs to be timezone-aware.

    Args:
        config_start (datetime): Desired start time (aware).
        config_stop (datetime): Desired stop time (aware).
        server_start (datetime): Server available start time (aware).
        server_stop (datetime): Server available stop time (aware).

    Returns:
        Optional[Tuple[datetime, datetime]]: The (start, stop) of the
                                             overlapping period, or None
                                             if no overlap.
    """
    valid_start = max(config_start, server_start)
    valid_stop = min(config_stop, server_stop)

    if valid_start < valid_stop:
        return (valid_start, valid_stop)
    else:
        return None


class GenericHapiClient:
    """
    A generic HAPI client that uses a master catalog to find and
    stream data from any HAPI server. Implements server fallback logic.
    """

    def __init__(self, catalog_path: pathlib.Path):
        """
        Initializes the client.

        Args:
            catalog_path (pathlib.Path): Path to the
                                         'hapi_master_catalog.json'.
        """
        self.catalog_map = self._load_catalog(catalog_path)
        if self.catalog_map:
            logger.info(f"Loaded HAPI master catalog with {len(self.catalog_map)} entries.")

    def _load_catalog(self, catalog_path: pathlib.Path) -> Dict[str, List[Dict]]:
        """Loads the master catalog JSON file into a dictionary."""
        if not catalog_path.exists():
            logger.error(f"HAPI master catalog not found at: {catalog_path}")
            logger.error("Please run 'python -m space_weather_analysis.main' with 'build_hapi_catalog' uncommented.")
            return {}
        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load HAPI master catalog: {e}")
            return {}

    def get_catalog_entries(self, dataset_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Finds the list of catalog entries (servers and time ranges)
        for a given dataset ID.

        Args:
            dataset_id (str): The dataset ID to find.

        Returns:
            Optional[List[Dict[str, Any]]]: A list of server entries, or None.
        """
        entries = self.catalog_map.get(dataset_id)
        if not entries:
            logger.error(f"Dataset '{dataset_id}' not found in HAPI master catalog.")
        return entries

    def get_data_and_meta(
        self,
        dataset_id: str,
        config_start: datetime,
        config_stop: datetime,
        parameters: Optional[List[str]] = None,
    ) -> Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
        """
        Fetches both data and metadata from the correct HAPI server.

        Implements fallback logic: It iterates through all known servers
        for the dataset, checks for time overlap, and tries to download
        until one succeeds.

        Args:
            dataset_id (str): The dataset ID to fetch.
            config_start (datetime): The desired start time (must be aware).
            config_stop (datetime): The desired end time (must be aware).
            parameters (Optional[List[str]]): Specific parameters to fetch.

        Returns:
            Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
                - The data as a NumPy structured array.
                - The metadata dictionary (with 'x_server_url' added).
                Returns (None, None) if all fallbacks fail.
        """
        entries = self.get_catalog_entries(dataset_id)
        if not entries:
            return None, None

        params_str = ",".join(parameters) if parameters else ""

        for i, entry in enumerate(entries):
            server_url = entry.get("server")
            time_range = entry.get("time_range")

            logger.info(f"Attempting source {i + 1}/{len(entries)} for {dataset_id} from: {server_url}")

            if not server_url or not time_range:
                logger.warning("Skipping entry: catalog entry is incomplete.")
                continue

            # Check Time Intersection
            server_start = _parse_hapi_time(time_range.get("start_time"))
            server_stop = _parse_hapi_time(time_range.get("end_time"))

            valid_range = _calculate_time_intersection(config_start, config_stop, server_start, server_stop)

            if not valid_range:
                logger.warning(
                    f"Skipping server {server_url}: No time overlap. Available: {server_start} to {server_stop}."
                )
                continue

            valid_start, valid_stop = valid_range
            logger.info(f"Requesting valid intersection: {valid_start} to {valid_stop}")

            # Attempt Data Download
            try:
                start_str = valid_start.isoformat(timespec="seconds").replace("+00:00", "Z")
                stop_str = valid_stop.isoformat(timespec="seconds").replace("+00:00", "Z")

                logger.debug(f"HAPI Request: dataset={dataset_id}, start={start_str}, stop={stop_str}")

                data, meta = hapi(server_url, dataset_id, params_str, start_str, stop_str, logging=True)

                logger.info(f"Successfully fetched {dataset_id} from {server_url}")
                meta["x_server_url"] = server_url  # Add provenance
                return data, meta

            except HAPIError as e:
                logger.error(
                    f"HAPI data request failed for {dataset_id} at {server_url}. Server responded: {e}",
                    exc_info=True,
                )
                # Continue to the next server (fallback)
            except Exception as e:
                logger.error(
                    f"Unexpected error during HAPI request for {dataset_id} from {server_url}: {e}",
                    exc_info=True,
                )

        logger.error(f"All fallbacks failed for dataset '{dataset_id}'.")
        return None, None

    def get_metadata(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetches only the metadata (info) for a dataset.
        Tries all fallbacks until one succeeds.

        Args:
            dataset_id (str): The dataset ID.

        Returns:
            Optional[Dict[str, Any]]: The metadata dictionary or None.
        """
        entries = self.get_catalog_entries(dataset_id)
        if not entries:
            return None

        for entry in entries:
            server_url = entry.get("server")
            if not server_url:
                continue

            try:
                meta = hapi(server_url, dataset_id, logging=True)
                meta["x_server_url"] = server_url  # Add provenance
                return meta  # Return on first success
            except HAPIError:
                # Logged as debug, as this is expected during fallback
                logger.debug(f"Metadata fallback failed for {dataset_id} at {server_url}.")
                continue

        logger.error(f"Could not retrieve metadata for {dataset_id} from any server.")
        return None
