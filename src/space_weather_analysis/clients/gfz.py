import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from space_weather_analysis.clients.base import BaseClient
from space_weather_analysis.util.types import CatalogEntryDict

logger = logging.getLogger(__name__)


class GfzClient(BaseClient):
    """Client for interacting with the GFZ Potsdam Kp Index API.

    This client fetches geomagnetic index data (specifically Kp) to provide
    context for solar events. It implements the BaseClient interface to fit
    into the broader data collection architecture.
    """

    BASE_URL = "https://kp.gfz.de/app/json/"
    SOURCE_TYPE = "GFZ_POTSDAM"

    # Index names the GFZ web service accepts; any other name is answered with HTTP 500.
    # Taken from getKpindex.py by I. Wehner, GFZ (CC BY 4.0): https://kp.gfz.de/app/webservice/python
    ALLOWED_INDICES = frozenset({"Kp", "ap", "Ap", "Cp", "C9", "Hp30", "Hp60", "ap30", "ap60", "SN", "Fobs", "Fadj"})

    def __init__(self) -> None:
        """Initializes the client with a resilient session strategy."""
        self.session = requests.Session()
        # Configure retry strategy to handle transient network issues
        retries = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def get_catalog_entries(self) -> List[CatalogEntryDict]:
        """Returns the available indices as catalog entries.

        Since GFZ offers a specific set of indices, we expose the primary 'Kp'
        index as a catalog entry to be discoverable by the collector.

        Returns:
            List[CatalogEntryDict]: A list containing the Kp index definition.
        """
        return [
            {
                "type": self.SOURCE_TYPE,
                "dataset_id": "Kp",
                "source_id": "GFZ",
                "description": "Planetary K-index (3-hourly) from GFZ Potsdam.",
                "time_range": {"start_time": "1932-01-01", "end_time": datetime.now(timezone.utc).strftime("%Y-%m-%d")},
            }
        ]

    def get_dataset_metadata(self, dataset_id: str) -> Dict[str, Any]:
        """Retrieves metadata for the specified index.

        Args:
            dataset_id (str): The GFZ index name.

        Returns:
            Dict[str, Any]: Metadata including units and description.
        """
        return {"id": dataset_id, "source": self.SOURCE_TYPE, "units": "Index (0-9)", "description": "Global geomagnetic storm index."}

    def get_data(self, dataset_id: str, start_time: datetime, end_time: datetime, **kwargs: Any) -> pd.DataFrame:
        """Fetches index data for the specified time range.

        The API includes values at end_time, and each timestamp marks the start of its interval in UTC.

        Args:
            dataset_id (str): The GFZ index name to fetch.
            start_time (datetime): Start of the window (inclusive).
            end_time (datetime): End of the window (inclusive).
            **kwargs: Optional 'status' value forwarded as the API query parameter; other keys are ignored.

        Returns:
            pd.DataFrame: DataFrame indexed by 'datetime' with the index values, missing values as NaN,
                          and a 'status' column ('def' definitive, 'pre' preliminary) if the response has one.
                          The 'meta' object of the response is available in DataFrame.attrs["meta"].
                          Returns empty DataFrame on failure.
        """
        # Checked before the request, as the service answers unknown names with HTTP 500, which the session retries
        if dataset_id not in self.ALLOWED_INDICES:
            logger.error(f"Unknown GFZ index '{dataset_id}'; allowed are {sorted(self.ALLOWED_INDICES)}.")
            return pd.DataFrame()

        # Ensure correct string format for the API
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        start_str = start_time.strftime(fmt)
        end_str = end_time.strftime(fmt)

        params = {"start": start_str, "end": end_str, "index": dataset_id}

        # The optional 'status' parameter is only sent if it is passed in kwargs
        if "status" in kwargs:
            params["status"] = kwargs["status"]

        logger.info(f"Fetching {dataset_id} from GFZ: {start_str} to {end_str}")

        try:
            response = self.session.get(self.BASE_URL, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            # The API returns a dictionary with lists. We confirm the keys exist.
            if not isinstance(data, dict):
                logger.warning(f"Response for {dataset_id} is not a JSON object.")
                return pd.DataFrame()
            if "datetime" not in data or dataset_id not in data:
                logger.warning(f"Response missing expected keys: {data.keys()}")
                return pd.DataFrame()

            columns: Dict[str, Any] = {"datetime": pd.to_datetime(data["datetime"], utc=True), dataset_id: data[dataset_id]}
            # GFZ sends the status only for some indices
            if "status" in data:
                columns["status"] = data["status"]
            df = pd.DataFrame(columns)

            df[dataset_id] = parse_index_values(df[dataset_id])

            # Set index to datetime for easier joining/plotting later
            df.set_index("datetime", inplace=True)

            df.attrs["meta"] = data.get("meta", {})

            return df

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error fetching GFZ data: {e}")
            return pd.DataFrame()
        except ValueError as e:
            logger.error(f"Error parsing GFZ response: {e}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"Unexpected error in GfzClient: {e}", exc_info=True)
            return pd.DataFrame()


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
