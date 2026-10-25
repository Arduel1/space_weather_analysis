import logging
from datetime import datetime
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

    BASE_URL = "https://kp.gfz-potsdam.de/app/json/"
    SOURCE_TYPE = "GFZ_POTSDAM"

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
                "time_range": {"start_time": "1932-01-01", "end_time": datetime.utcnow().strftime("%Y-%m-%d")},
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
        """Fetches Kp index data for the specified time range.

        Args:
            dataset_id (str): The GFZ index name to fetch.
            start_time (datetime): Start of the window.
            end_time (datetime): End of the window.
            **kwargs: Optional 'status' value forwarded as the API query parameter; other keys are ignored.

        Returns:
            pd.DataFrame: DataFrame containing 'datetime' and the index values.
                          Returns empty DataFrame on failure.
        """
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
            if "datetime" not in data or dataset_id not in data:
                logger.warning(f"Response missing expected keys: {data.keys()}")
                return pd.DataFrame()

            df = pd.DataFrame({"datetime": pd.to_datetime(data["datetime"]), dataset_id: data[dataset_id]})

            # Ensure numeric conversion for the index column
            df[dataset_id] = pd.to_numeric(df[dataset_id], errors="coerce")

            # Set index to datetime for easier joining/plotting later
            df.set_index("datetime", inplace=True)

            return df

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error fetching GFZ data: {e}")
            return pd.DataFrame()
        except ValueError as e:
            logger.error(f"Error parsing JSON response: {e}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"Unexpected error in GfzClient: {e}", exc_info=True)
            return pd.DataFrame()
