import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.base import DataClient, FetchResult
from space_weather_analysis.clients.download import download_json
from space_weather_analysis.clients.swpc_products import SwpcProduct, configured_swpc_products
from space_weather_analysis.util.error_handling import DataProcessingError
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)

# Time a file may fall short of the requested start and still be chosen: a job starts its lookback when it is queued,
# so the start of a range as long as a file lies a moment before what the file still holds when it is fetched
FILE_START_TOLERANCE = timedelta(minutes=10)


class SwpcClient(DataClient[pd.DataFrame]):
    """Fetches the rolling JSON files of the NOAA Space Weather Prediction Center (https://services.swpc.noaa.gov/).

    SWPC offers no queries by time range, only files of the latest hours or days, so a range further back than the
    longest file gets only the part the file covers. Which files make up a dataset is described in the products file.
    """

    source_type = SourceType.SWPC

    DEFAULT_BASE_URL = "https://services.swpc.noaa.gov/"
    # Connect and read timeout in seconds
    TIMEOUT = (10.0, 30.0)
    DEFAULT_RETRY_COUNT = 2
    # Statuses of an overloaded or failing server that a repeated request may not get
    RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        products: Mapping[str, SwpcProduct] | None = None,
        base_url: str = DEFAULT_BASE_URL,
        retry_count: int = DEFAULT_RETRY_COUNT,
        retry_backoff_factor: float = 1.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initializes the client with a session that repeats requests failing with a server error.

        Args:
            products: The products by dataset ID; None reads the products file of the settings.
            base_url: The URL of the services, ending with a slash; the paths of the product files lie below it.
            retry_count: The number of repetitions of a request failing with a server error.
            retry_backoff_factor: The wait before the first repetition in seconds, doubled for every further one.
            clock: Returns the current time in UTC, the end of every rolling file.

        Raises:
            OSError: If the products file of the settings cannot be read.
            tomllib.TOMLDecodeError: If the products file is no valid TOML.
            pydantic.ValidationError: If a product is invalid.
        """
        self.products: Mapping[str, SwpcProduct] = products if products is not None else configured_swpc_products()
        self.base_url = base_url
        self.clock = clock
        self.session = requests.Session()
        retries = Retry(total=retry_count, backoff_factor=retry_backoff_factor, status_forcelist=sorted(self.RETRY_STATUSES))
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def fetch(self, dataset_id: str, start: datetime, stop: datetime) -> FetchResult[pd.DataFrame]:
        """Fetches the shortest file of a dataset that reaches back to the start, and keeps its rows inside the range.

        The rows of a model run reaching ahead are kept from the start on, those after the end included.

        Args:
            dataset_id: The dataset ID, a key of the products.
            start: The start of the range (inclusive), timezone-aware.
            stop: The end of the range (exclusive), timezone-aware.

        Returns:
            One row per time with a UTC DatetimeIndex; a result without data if the file holds no row in the range.

        Raises:
            ValueError: If the products hold no dataset of this ID.
            SourceUnreachableError: If SWPC sent no answer.
            DataRetrievalError: If the download failed.
            DataProcessingError: If the file is no list of rows with the expected fields.
        """
        product = self.products.get(dataset_id)
        if product is None:
            raise ValueError(f"Unknown SWPC dataset '{dataset_id}'; known are {sorted(self.products)}.")
        start, stop = ensure_aware(start), ensure_aware(stop)
        url = self.base_url + _covering_file(product, start, self.clock())
        logger.info("Fetching %s from SWPC: %s", dataset_id, url)

        content: Any = download_json(self.session, url, self.TIMEOUT, f"SWPC file of {dataset_id}")
        if not isinstance(content, list):
            raise DataProcessingError(f"SWPC file {url} is no list of rows.")
        try:
            frame = product.parse(pd.DataFrame(content))
        except (KeyError, AttributeError) as error:
            raise DataProcessingError(f"SWPC file {url} lacks an expected field: {error}") from error

        in_range = frame[(frame.index >= start) & ((frame.index < stop) | product.reaches_ahead)]
        if in_range.empty:
            return FetchResult(None, url)
        return FetchResult(in_range, url)


def _covering_file(product: SwpcProduct, start: datetime, now: datetime) -> str:
    """Selects the shortest file of a product that reaches back to the start within FILE_START_TOLERANCE, or the longest one if none does.

    Args:
        product: The product.
        start: The start of the requested range.
        now: The current time, the end of every file.

    Returns:
        The path of the file below the base URL.
    """
    for file in product.files:
        if now - file.length <= start + FILE_START_TOLERANCE:
            return file.path
    return product.files[-1].path
