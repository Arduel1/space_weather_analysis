import logging
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any, ClassVar

import numpy as np
import requests
from hapiclient import hapi
from hapiclient.util import HAPIError

from space_weather_analysis.catalog.models import CatalogEntryDict, CatalogMap, SourceType
from space_weather_analysis.clients.base import DataClient, FetchResult
from space_weather_analysis.util.error_handling import DataRetrievalError, SourceUnreachableError
from space_weather_analysis.util.resilience import retry_interaction
from space_weather_analysis.util.time_utils import calculate_time_intersection, parse_iso_time

logger = logging.getLogger(__name__)

# The data and the /info response of one HAPI data request, as hapiclient returns them.
HapiData = tuple[np.ndarray, dict[str, Any]]


class HapiClient(DataClient[HapiData]):
    """Fetches data from the HAPI servers the master catalog lists for each dataset, falling back from one server to the next.

    HAPI (Heliophysics Application Programmer's Interface, https://hapi-server.org/) servers deliver
    every parameter of a dataset for a time range. A server that fails transiently is asked again
    before the next server is tried.
    """

    source_type = SourceType.HAPI

    # Options for every hapi() call: no print output, no files in the hapiclient disk cache,
    # and CSV transfer, which every HAPI server supports and which spares the /capabilities request.
    HAPI_OPTIONS: ClassVar[Mapping[str, Any]] = MappingProxyType({"logging": False, "cache": False, "format": "csv"})

    # hapiclient appends this hint to the errors it raises itself (connection problems, error responses without a
    # HAPI status); errors without it carry the status message of a server that answered according to HAPI.
    HAPICLIENT_FAILURE_HINT = "hapi-server.org/servers"
    # Starts of the messages hapiclient raises when a server sent no answer: connection failed, refused or timed out
    HAPICLIENT_UNREACHABLE_PREFIXES = ("Connection error for", "Connection timeout for", "Failed to connect to", "Read timeout for")

    # An empty parameter list makes the server deliver every parameter of the dataset.
    ALL_PARAMETERS = ""

    def __init__(self, catalog_map: CatalogMap | None = None, retry_backoff_factor: float = 1.0) -> None:
        """Initializes the client with the HAPI sources it may request data from.

        Args:
            catalog_map: The HAPI sources of each dataset, keyed by dataset ID; None for a client without sources.
            retry_backoff_factor: The wait in seconds before asking a transiently failing source again, doubled for every further try.
        """
        self.catalog_map: CatalogMap = catalog_map if catalog_map is not None else {}
        self.retry_backoff_factor = retry_backoff_factor

    def fetch(self, dataset_id: str, start: datetime, stop: datetime) -> FetchResult[HapiData]:
        """Fetches every parameter of a dataset for a time range from the first catalog source that has data.

        Args:
            dataset_id: The dataset ID in the master catalog.
            start: The start of the range (inclusive), timezone-aware.
            stop: The end of the range (exclusive), timezone-aware.

        Returns:
            The data and /info response of the first source with data, or a result without data if no source has any.

        Raises:
            DataRetrievalError: If the catalog lists no HAPI source for the dataset, or if a source failed and no source delivered data.
            SourceUnreachableError: If no source sent an answer.
        """
        entries = self.catalog_map.get(dataset_id)
        if not entries:
            raise DataRetrievalError(f"The master catalog lists no HAPI source for dataset '{dataset_id}'.")

        failures: list[str] = []
        unreachable_count = 0
        for source_number, entry in enumerate(entries, start=1):
            server_url = entry.get("source_id", "")
            logger.info("Trying source %d/%d for %s: %s", source_number, len(entries), dataset_id, server_url)
            try:
                result = self._fetch_from_source(dataset_id, entry, start, stop)
            except DataRetrievalError as error:
                logger.warning("Source %s failed for %s: %s", server_url, dataset_id, error)
                failures.append(f"{server_url}: {error}")
                if isinstance(error, SourceUnreachableError):
                    unreachable_count += 1
                continue
            if result.data is not None:
                return result

        # A failed source may hold data the other sources lack, so the range counts as failed
        if failures:
            message = f"No source delivered data for '{dataset_id}' and {len(failures)} failed: {'; '.join(failures)}"
            # Only when no source answered at all, as an answering source may serve the next range
            if unreachable_count == len(entries):
                raise SourceUnreachableError(message)
            raise DataRetrievalError(message)
        logger.info("No source has data for %s between %s and %s.", dataset_id, start, stop)
        return FetchResult(None)

    def _fetch_from_source(self, dataset_id: str, entry: CatalogEntryDict, start: datetime, stop: datetime) -> FetchResult[HapiData]:
        """Fetches a time range from one catalog source, asking it again after a transient failure.

        Args:
            dataset_id: The dataset ID.
            entry: The catalog source with server URL and time range.
            start: The start of the requested range (inclusive), timezone-aware.
            stop: The end of the requested range (exclusive), timezone-aware.

        Returns:
            The data and /info response, or a result without data if the source has none for the range.

        Raises:
            DataRetrievalError: If the request failed transiently in every try.
        """
        retried_request = retry_interaction(exceptions=(DataRetrievalError,), backoff_factor=self.retry_backoff_factor)(self._request_source)
        return retried_request(dataset_id, entry, start, stop)

    def _request_source(self, dataset_id: str, entry: CatalogEntryDict, start: datetime, stop: datetime) -> FetchResult[HapiData]:
        """Fetches a time range from one catalog source once, clipped to the time range the catalog gives for it.

        The stop of an ongoing source is not clipped, as its server already holds data beyond the catalog stop.

        Args:
            dataset_id: The dataset ID.
            entry: The catalog source with server URL and time range.
            start: The start of the requested range (inclusive), timezone-aware.
            stop: The end of the requested range (exclusive), timezone-aware.

        Returns:
            The data and /info response, or a result without data if the source has none for the range.

        Raises:
            DataRetrievalError: If the request failed transiently.
        """
        server_url = entry.get("source_id")
        time_range = entry.get("time_range")
        if not server_url or not time_range:
            logger.warning("Skipping a source of %s without server URL or time range.", dataset_id)
            return FetchResult(None)

        server_start = parse_iso_time(time_range["start_time"])
        server_end = parse_iso_time(time_range["end_time"])
        if server_start is None or server_end is None:
            logger.warning("Skipping %s for %s: unparsable time range %s.", server_url, dataset_id, time_range)
            return FetchResult(None)

        available_end = stop if entry.get("ongoing") else server_end
        valid_range = calculate_time_intersection(start, stop, server_start, available_end)
        if not valid_range:
            logger.debug(
                "Skipping %s for %s: the catalog range %s to %s does not overlap the request.", server_url, dataset_id, server_start, server_end
            )
            return FetchResult(None)

        # HAPI requires the UTC designator 'Z'
        start_text = valid_range[0].isoformat(timespec="seconds").replace("+00:00", "Z")
        stop_text = valid_range[1].isoformat(timespec="seconds").replace("+00:00", "Z")
        logger.info("HAPI request: %s [%s -> %s] at %s", dataset_id, start_text, stop_text, server_url)

        try:
            data, meta = hapi(server_url, dataset_id, self.ALL_PARAMETERS, start_text, stop_text, **self.HAPI_OPTIONS)
        except HAPIError as error:
            self._raise_if_transient(error, server_url, dataset_id)
            # Servers also report that no data exists with a non-200 HTTP status
            logger.info("Server %s has no data for %s [%s -> %s]: %s", server_url, dataset_id, start_text, stop_text, error)
            return FetchResult(None, server_url)
        except (requests.ConnectionError, requests.Timeout) as error:
            raise SourceUnreachableError(f"{server_url} is unreachable for {dataset_id}: {error}") from error

        if data is None or len(data) == 0:
            logger.info("Server %s returned no data for %s [%s -> %s].", server_url, dataset_id, start_text, stop_text)
            return FetchResult(None, server_url)
        logger.info("Fetched %d records of %s from %s", len(data), dataset_id, server_url)
        return FetchResult((data, meta), server_url)

    def _raise_if_transient(self, error: HAPIError, server_url: str, dataset_id: str) -> None:
        """Raises a DataRetrievalError for a hapiclient error that marks a failed request instead of a server answer.

        Args:
            error: The error hapiclient raised.
            server_url: The server that was asked.
            dataset_id: The requested dataset.

        Raises:
            SourceUnreachableError: If the error says that the server sent no answer.
            DataRetrievalError: If the error carries the hint hapiclient adds to its own failures.
        """
        if str(error).startswith(self.HAPICLIENT_UNREACHABLE_PREFIXES):
            raise SourceUnreachableError(f"{server_url} is unreachable for {dataset_id}: {error}") from error
        if self.HAPICLIENT_FAILURE_HINT in str(error):
            raise DataRetrievalError(f"Request to {server_url} for {dataset_id} failed: {error}") from error
