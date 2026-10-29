import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from hapiclient import hapi
from hapiclient.util import HAPIError

from space_weather_analysis.catalog.models import CatalogEntryDict, CatalogMap
from space_weather_analysis.clients.base import BaseClient
from space_weather_analysis.util.resilience import retry_interaction
from space_weather_analysis.util.time_utils import calculate_time_intersection, parse_iso_time

logger = logging.getLogger(__name__)


class HapiClient(BaseClient):
    """Generic client that streams data from the HAPI servers listed in a catalog map.

    This client abstracts the HAPI protocol details and implements server
    fallback logic using the source list of each dataset. It handles metadata
    retrieval and data streaming for specified time ranges.
    """

    SOURCE_TYPE = "HAPI"

    # Options for every hapi() call: no print output, no files in the hapiclient disk cache,
    # and CSV transfer, which every HAPI server supports and which spares the /capabilities request.
    HAPI_OPTIONS: Dict[str, Any] = {"logging": False, "cache": False, "format": "csv"}

    # hapiclient appends this hint to the errors it raises itself (connection problems, error responses without a
    # HAPI status); errors without it carry the status message of a server that answered according to HAPI.
    HAPICLIENT_FAILURE_HINT = "hapi-server.org/servers"

    def __init__(self, catalog_map: Optional[CatalogMap] = None) -> None:
        """Initializes the client with the HAPI sources it may request data from.

        Args:
            catalog_map: The HAPI sources of each dataset, keyed by dataset ID; None for a client
                that only uses the methods taking a server URL.
        """
        self.catalog_map: CatalogMap = catalog_map if catalog_map is not None else {}

    # --- Discovery Methods ---

    @retry_interaction(max_retries=3, exceptions=(Exception,))
    def fetch_server_catalog(self, server_url: str) -> List[Dict[str, Any]]:
        """Fetches the full catalog from a specific HAPI server URL.

        Args:
            server_url: The base URL of the HAPI server.

        Returns:
            The list of datasets advertised by the server.
        """
        try:
            response = hapi(server_url, **self.HAPI_OPTIONS)
            catalog = response.get("catalog", [])
            logger.info(f"Discovered {len(catalog)} datasets at {server_url}")
            return catalog
        except Exception as e:
            logger.warning(f"Failed to fetch catalog from {server_url}: {e}")
            raise

    @retry_interaction(max_retries=3, exceptions=(Exception,))
    def fetch_dataset_metadata_from_server(self, dataset_id: str, server_url: str) -> Optional[Dict[str, Any]]:
        """Fetches metadata for a single dataset directly from a specific server.

        Args:
            dataset_id: The dataset ID to fetch.
            server_url: The URL of the HAPI server.

        Returns:
            The metadata dictionary or None on failure.
        """
        try:
            meta = hapi(server_url, dataset_id, **self.HAPI_OPTIONS)

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
        """Returns all datasets known to this client from its catalog map.

        Returns:
            A list of dataset entries suitable for the generic interface.
        """
        return [{"id": k, "sources": v} for k, v in self.catalog_map.items()]

    def get_dataset_metadata(self, dataset_id: str) -> Dict[str, Any]:
        """Fetches the metadata (HAPI 'info') for a dataset.

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
        """Fetches both data and metadata for a dataset within a time range.

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

    def _get_parameters_for_request(self, parameters: Optional[List[str]]) -> str:
        """Constructs the parameter string for the HAPI request.

        An empty string makes the server deliver all parameters of the dataset.

        Args:
            parameters: A list of requested parameter names, or None for all parameters.

        Returns:
            A comma-separated string of parameters.
        """
        if parameters:
            return ",".join(parameters)
        return ""

    def get_data_and_meta(
        self,
        dataset_id: str,
        config_start: datetime,
        config_end: datetime,
        parameters: Optional[List[str]] = None,
    ) -> Tuple[Optional[np.ndarray], Optional[Dict[str, Any]]]:
        """Fetches both data and metadata using server fallback logic.

        Args:
            dataset_id: The dataset ID to fetch.
            config_start: The desired start time (must be aware).
            config_end: The desired end time (must be aware).
            parameters: Specific parameters to fetch.

        Returns:
            (data, meta) on success, or (None, None) if no source delivered data.
        """
        entries = self.catalog_map.get(dataset_id)
        if not entries:
            logger.error(f"Cannot fetch data: Dataset {dataset_id} has no catalog entries.")
            return None, None

        params_str = self._get_parameters_for_request(parameters)

        for i, entry in enumerate(entries):
            source_id = entry.get("source_id", "Unknown")
            logger.info(f"Attempting source {i + 1}/{len(entries)} for {dataset_id}: {source_id}")

            data, meta = self._attempt_server_entry(dataset_id, entry, config_start, config_end, params_str)

            if data is not None and meta is not None:
                return data, meta

        logger.info(f"No source delivered data for dataset '{dataset_id}' between {config_start} and {config_end}.")
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
        """Attempts data download from a single server entry with retry logic.

        Args:
            dataset_id: The dataset ID.
            entry: The catalog entry containing source_id and time_range; the request is clipped to the time range,
                except for the end of an ongoing source.
            config_start: The requested start time.
            config_end: The requested end time.
            params_str: Comma-separated parameters.

        Returns:
            (data, meta) on success, or (None, None) if this server delivered no data.
        """
        server_url = entry.get("source_id")
        time_range = entry.get("time_range")

        if not server_url or not time_range:
            logger.warning("Skipping entry: incomplete metadata.")
            return None, None

        server_start = parse_iso_time(time_range["start_time"])
        server_end = parse_iso_time(time_range["end_time"])
        if server_start is None or server_end is None:
            logger.warning(f"Skipping {server_url}: unparsable time range {time_range}.")
            return None, None

        # The catalog stop of an ongoing source lags behind the data the server already has
        available_end = config_end if entry.get("ongoing") else server_end
        valid_range = calculate_time_intersection(config_start, config_end, server_start, available_end)

        if not valid_range:
            logger.debug(f"Skipping {server_url}: No overlap. Req: {config_start}-{config_end}, Avail: {server_start}-{server_end}")
            return None, None

        valid_start, valid_stop = valid_range

        # HAPI requires 'Z' for UTC time
        start_str = valid_start.isoformat(timespec="seconds").replace("+00:00", "Z")
        stop_str = valid_stop.isoformat(timespec="seconds").replace("+00:00", "Z")

        logger.info(f"{self.SOURCE_TYPE} Request: {dataset_id} [{start_str} -> {stop_str}]")

        try:
            # The decorator handles retries for network glitches.
            data, meta = hapi(server_url, dataset_id, params_str, start_str, stop_str, **self.HAPI_OPTIONS)

            if data is None or len(data) == 0:
                logger.info(f"Server {server_url} returned no data for {dataset_id} [{start_str} -> {stop_str}].")
                return None, None

            logger.info(f"Successfully fetched {len(data)} records from {server_url}")
            meta["x_server_url"] = server_url
            return data, meta

        except HAPIError as e:
            # hapiclient raises HAPIError for connection problems and for every non-200 HTTP status, which servers
            # also use to report that no data exists for the request; another source may still have the data.
            if self.HAPICLIENT_FAILURE_HINT in str(e):
                logger.warning(f"Request to {server_url} failed for {dataset_id} [{start_str} -> {stop_str}]: {e}")
            else:
                logger.info(f"Server {server_url} delivered no data for {dataset_id} [{start_str} -> {stop_str}]: {e}")
            return None, None

        except Exception as e:
            # Unexpected exceptions are re-raised so the retry decorator catches them
            logger.warning(f"Transient error fetching from {server_url}: {e}")
            raise
