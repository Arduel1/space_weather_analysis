import logging
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from space_weather_analysis.catalog.models import CatalogEntryDict, SourceType
from space_weather_analysis.clients.base import DataClient, FetchResult
from space_weather_analysis.util.error_handling import DataProcessingError, DataRetrievalError
from space_weather_analysis.util.resilience import RETRY_AFTER_STATUSES, retry_after_seconds
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)


class GfzClient(DataClient[pd.DataFrame]):
    """Fetches geomagnetic indices from the JSON web service of GFZ Potsdam (https://kp.gfz.de/)."""

    source_type = SourceType.GFZ_POTSDAM

    BASE_URL = "https://kp.gfz.de/app/json/"

    # Index names the GFZ web service accepts; any other name is answered with HTTP 500.
    # Taken from getKpindex.py by I. Wehner, GFZ (CC BY 4.0): https://kp.gfz.de/app/webservice/python
    ALLOWED_INDICES = frozenset({"Kp", "ap", "Ap", "Cp", "C9", "Hp30", "Hp60", "ap30", "ap60", "SN", "Fobs", "Fadj"})

    TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
    TIMEOUT_SECONDS = 10
    RETRY_COUNT = 3
    # Statuses of an overloaded or failing server that a repeated request may not get
    RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(self, retry_backoff_factor: float = 1.0) -> None:
        """Initializes the client with a session that repeats requests failing with a server error.

        Args:
            retry_backoff_factor: The wait before the first repetition in seconds, doubled for every further one.
        """
        self.session = requests.Session()
        retries = Retry(total=self.RETRY_COUNT, backoff_factor=retry_backoff_factor, status_forcelist=sorted(self.RETRY_STATUSES))
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    @staticmethod
    def get_catalog_entries() -> list[CatalogEntryDict]:
        """Lists the Kp index as the catalog entry of GFZ.

        Returns:
            The catalog entry of the Kp index, available from 1932 until today.
        """
        return [
            {
                "type": SourceType.GFZ_POTSDAM,
                "dataset_id": "Kp",
                "source_id": "GFZ",
                "description": "Planetary K-index (3-hourly) from GFZ Potsdam.",
                "time_range": {"start_time": "1932-01-01", "end_time": datetime.now(UTC).strftime("%Y-%m-%d")},
            }
        ]

    @staticmethod
    def get_dataset_metadata(dataset_id: str) -> dict[str, Any]:
        """Describes an index for the discovery.

        Args:
            dataset_id: The GFZ index name.

        Returns:
            The index name, source type, unit and description.
        """
        return {"id": dataset_id, "source": SourceType.GFZ_POTSDAM, "units": "Index (0-9)", "description": "Global geomagnetic storm index."}

    def fetch(self, dataset_id: str, start: datetime, stop: datetime) -> FetchResult[pd.DataFrame]:
        """Fetches the values of an index for a time range.

        The API includes the value at its end time, so values at stop are dropped. Each time stamp marks
        the start of its interval.

        Args:
            dataset_id: The GFZ index name.
            start: The start of the range (inclusive); a naive value is read as UTC.
            stop: The end of the range (exclusive); a naive value is read as UTC.

        Returns:
            A DataFrame indexed by the aware interval start times with the index values (missing values as NaN),
            a 'status' column ('def' definitive, 'pre' preliminary) if GFZ sends one, and the 'meta' object of the
            response in DataFrame.attrs["meta"]; a result without data if the range holds no value.

        Raises:
            ValueError: If GFZ offers no index of this name.
            DataRetrievalError: If the request failed.
            DataProcessingError: If the response is no index data.
        """
        if dataset_id not in self.ALLOWED_INDICES:
            raise ValueError(f"Unknown GFZ index '{dataset_id}'; allowed are {sorted(self.ALLOWED_INDICES)}.")

        start = ensure_aware(start)
        stop = ensure_aware(stop)
        params = {
            "start": start.astimezone(UTC).strftime(self.TIME_FORMAT),
            "end": stop.astimezone(UTC).strftime(self.TIME_FORMAT),
            "index": dataset_id,
        }
        logger.info("Fetching %s from GFZ: %s to %s", dataset_id, params["start"], params["end"])

        try:
            response = self.session.get(self.BASE_URL, params=params, timeout=self.TIMEOUT_SECONDS)
        except requests.RequestException as error:
            raise DataRetrievalError(f"GFZ request for {dataset_id} failed: {error}") from error
        if not response.ok:
            retry_after = retry_after_seconds(response.headers.get("Retry-After")) if response.status_code in RETRY_AFTER_STATUSES else None
            raise DataRetrievalError(f"GFZ answered HTTP {response.status_code} for {dataset_id}", retry_after)

        try:
            body = response.json()
        except ValueError as error:
            raise DataProcessingError(f"GFZ response for {dataset_id} is no JSON: {error}") from error
        if not isinstance(body, dict) or "datetime" not in body or dataset_id not in body:
            raise DataProcessingError(f"GFZ response for {dataset_id} lacks the keys 'datetime' and '{dataset_id}'.")

        frame = _index_frame(body, dataset_id)
        in_range = frame[(frame.index >= start) & (frame.index < stop)]
        if in_range.empty:
            return FetchResult(None, self.BASE_URL)
        in_range.attrs["meta"] = body.get("meta", {})
        return FetchResult(in_range, self.BASE_URL)


def _index_frame(body: dict[str, Any], dataset_id: str) -> pd.DataFrame:
    """Builds the DataFrame of an index from the lists of a GFZ response.

    Args:
        body: The parsed response with the lists 'datetime', the index and optionally 'status'.
        dataset_id: The GFZ index name.

    Returns:
        The values indexed by their aware time stamps.

    Raises:
        DataProcessingError: If the lists cannot be combined into a table.
    """
    columns: dict[str, Any] = {"datetime": pd.to_datetime(body["datetime"], utc=True), dataset_id: body[dataset_id]}
    # GFZ sends the status only for some indices
    if "status" in body:
        columns["status"] = body["status"]
    try:
        frame = pd.DataFrame(columns)
    except ValueError as error:
        raise DataProcessingError(f"GFZ response for {dataset_id} has lists of different lengths: {error}") from error
    frame[dataset_id] = parse_index_values(frame[dataset_id])
    return frame.set_index("datetime")


def parse_index_values(values: pd.Series) -> pd.Series:
    """Converts raw GFZ index values to floats with missing values as NaN.

    GFZ marks a missing value with -1 or null; every GFZ index is non-negative,
    so -1 is never a valid value.

    Args:
        values: The raw index values of the response.

    Returns:
        The values as floats, with -1, null and non-numeric entries as NaN.
    """
    numeric_values = pd.to_numeric(values, errors="coerce").astype(float)
    return numeric_values.mask(numeric_values == -1)
