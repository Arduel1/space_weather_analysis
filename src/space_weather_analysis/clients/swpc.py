import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, cast

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.base import DataClient, FetchResult
from space_weather_analysis.clients.json_download import download_json
from space_weather_analysis.util.error_handling import DataProcessingError
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)

# Field names of the GOES X-ray channels, by the wavelength band SWPC names in `energy`; 0.1-0.8 nm defines the flare class
XRAY_CHANNELS: Mapping[str, str] = MappingProxyType({"0.1-0.8nm": "long", "0.05-0.4nm": "short"})
# Lengths of the rolling GOES files SWPC offers, with the suffix of their file names
GOES_WINDOWS: tuple[tuple[timedelta, str], ...] = (
    (timedelta(hours=6), "6-hour"),
    (timedelta(days=1), "1-day"),
    (timedelta(days=3), "3-day"),
    (timedelta(days=7), "7-day"),
)
# Time a file may fall short of the requested start and still be chosen: a job starts its lookback when it is queued,
# so the start of a range as long as a file lies a moment before what the file still holds when it is fetched
FILE_START_TOLERANCE = timedelta(minutes=10)


def _utc_times(rows: pd.DataFrame) -> pd.Series:
    """Parses the `time_tag` column as UTC times; SWPC leaves out the zone designator in some files, which then count as UTC."""
    return pd.to_datetime(rows["time_tag"], utc=True, errors="coerce")


def _channels_to_columns(rows: pd.DataFrame, channel_names: pd.Series, values: Mapping[str, str]) -> pd.DataFrame:
    """Turns rows with one channel each into one row per time with one column per channel and value.

    Args:
        rows: The rows of the file with `time_tag` and `satellite`.
        channel_names: The field name prefix of the channel of each row; rows without a name are left out.
        values: The value columns of the rows to keep, mapped to the field name suffix.

    Returns:
        One row per time, sorted, with the columns `{channel}_{suffix}` and `satellite`, the satellite of the first row of the time.
    """
    named = rows.assign(time=_utc_times(rows), channel=channel_names).dropna(subset=["time", "channel"])
    wide = named.pivot_table(index="time", columns="channel", values=list(values), aggfunc="first")
    # The pivot names each column by the pair of value column and channel, which the pandas stubs type as plain labels
    column_pairs = cast("list[tuple[str, str]]", wide.columns.tolist())
    wide.columns = [f"{channel}_{values[value]}" for value, channel in column_pairs]
    wide["satellite"] = named.groupby("time")["satellite"].first()
    wide.index.name = None
    return wide.sort_index()


def parse_xrays(rows: pd.DataFrame) -> pd.DataFrame:
    """Converts the rows of a GOES X-ray file into one row per minute.

    Args:
        rows: The file rows with `time_tag`, `satellite`, `flux`, `electron_contaminaton` (spelled so by SWPC) and `energy`.

    Returns:
        The columns `long_flux` and `short_flux` in W/m^2, `long_electron_contamination`, `short_electron_contamination` and `satellite`.
    """
    return _channels_to_columns(rows, rows["energy"].map(XRAY_CHANNELS), {"flux": "flux", "electron_contaminaton": "electron_contamination"})


def parse_integral_protons(rows: pd.DataFrame) -> pd.DataFrame:
    """Converts the rows of a GOES integral proton file into one row per time.

    Args:
        rows: The file rows with `time_tag`, `satellite`, `flux` and `energy` written as `>={number} MeV`.

    Returns:
        One column `p_ge{number}mev_flux` per energy threshold in pfu, and `satellite`.
    """
    thresholds = rows["energy"].str.extract(r"^>=(\d+) MeV$", expand=False)
    return _channels_to_columns(rows, "p_ge" + thresholds + "mev", {"flux": "flux"})


def parse_estimated_kp(rows: pd.DataFrame) -> pd.DataFrame:
    """Converts the rows of the 1-minute estimated planetary K index file.

    Args:
        rows: The file rows with `time_tag`, `estimated_kp` and `kp_index`.

    Returns:
        One row per minute with `estimated_kp` (Kp estimated from the current minute, not the official Kp) and `kp_index` (its integer part).
    """
    frame = rows.assign(time=_utc_times(rows)).dropna(subset=["time"]).set_index("time")[["estimated_kp", "kp_index"]]
    frame.index.name = None
    return frame.sort_index()


@dataclass(frozen=True)
class SwpcVariable:
    """The description of one stored field of an SWPC product, which SWPC publishes no metadata for.

    Attributes:
        description: What the field holds.
        units: The unit of the values; None for values without unit.
    """

    description: str
    units: str | None = None


@dataclass(frozen=True)
class SwpcProduct:
    """A data product SWPC publishes as rolling JSON files.

    Attributes:
        files: The length of each file and its path below the base URL, by ascending length.
        parse: Converts the file rows into one row per time.
        provider_id: The product name in the source identifier of the documents.
        title: The title of the dataset.
        cadence: The time between two values.
        variables: The description of each stored field, by field name.
    """

    files: tuple[tuple[timedelta, str], ...]
    parse: Callable[[pd.DataFrame], pd.DataFrame]
    provider_id: str
    title: str
    cadence: timedelta
    variables: Mapping[str, SwpcVariable]


def _goes_files(product: str) -> tuple[tuple[timedelta, str], ...]:
    """Lists the rolling files of a product of the primary GOES satellite."""
    return tuple((length, f"goes/primary/{product}-{suffix}.json") for length, suffix in GOES_WINDOWS)


# Energy thresholds in MeV of the GOES integral proton channels SWPC publishes
PROTON_THRESHOLDS_MEV = (1, 5, 10, 30, 50, 60, 100, 500)
_SATELLITE = SwpcVariable("Number of the GOES satellite that measured the values")
_XRAY_VARIABLES = {
    "long_flux": SwpcVariable("X-ray flux 0.1-0.8 nm, the band that defines the flare class", "W/m^2"),
    "short_flux": SwpcVariable("X-ray flux 0.05-0.4 nm", "W/m^2"),
    "long_electron_contamination": SwpcVariable("Whether electrons contaminate the 0.1-0.8 nm value"),
    "short_electron_contamination": SwpcVariable("Whether electrons contaminate the 0.05-0.4 nm value"),
    "satellite": _SATELLITE,
}
_PROTON_VARIABLES = {
    **{f"p_ge{threshold}mev_flux": SwpcVariable(f"Integral proton flux >={threshold} MeV", "pfu") for threshold in PROTON_THRESHOLDS_MEV},
    "satellite": _SATELLITE,
}
_KP_VARIABLES = {
    "estimated_kp": SwpcVariable("Planetary K index estimated every minute by SWPC, not the official Kp"),
    "kp_index": SwpcVariable("Integer part of the estimated planetary K index"),
}

# Datasets served from SWPC, by dataset ID
SWPC_PRODUCTS: Mapping[str, SwpcProduct] = MappingProxyType(
    {
        "swpc_goes_xray": SwpcProduct(
            _goes_files("xrays"), parse_xrays, "goes-primary-xrays", "GOES primary X-ray flux (1-minute)", timedelta(minutes=1), _XRAY_VARIABLES
        ),
        "swpc_goes_protons": SwpcProduct(
            _goes_files("integral-protons"),
            parse_integral_protons,
            "goes-primary-integral-protons",
            "GOES primary integral proton flux (5-minute)",
            timedelta(minutes=5),
            _PROTON_VARIABLES,
        ),
        "swpc_kp_estimated": SwpcProduct(
            ((timedelta(hours=6), "planetary_k_index_1m.json"),),
            parse_estimated_kp,
            "planetary-k-index-1m",
            "Estimated planetary K index (1-minute)",
            timedelta(minutes=1),
            _KP_VARIABLES,
        ),
    }
)


class SwpcClient(DataClient[pd.DataFrame]):
    """Fetches the rolling JSON files of the NOAA Space Weather Prediction Center (https://services.swpc.noaa.gov/json/).

    SWPC offers no queries by time range, only files of the latest hours or days, so a range further back than the
    longest file gets only the part the file covers.
    """

    source_type = SourceType.SWPC

    DEFAULT_BASE_URL = "https://services.swpc.noaa.gov/json/"
    # Connect and read timeout in seconds
    TIMEOUT = (10.0, 30.0)
    DEFAULT_RETRY_COUNT = 2
    # Statuses of an overloaded or failing server that a repeated request may not get
    RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        retry_count: int = DEFAULT_RETRY_COUNT,
        retry_backoff_factor: float = 1.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initializes the client with a session that repeats requests failing with a server error.

        Args:
            base_url: The URL of the JSON services, ending with a slash.
            retry_count: The number of repetitions of a request failing with a server error.
            retry_backoff_factor: The wait before the first repetition in seconds, doubled for every further one.
            clock: Returns the current time in UTC, the end of every rolling file.
        """
        self.base_url = base_url
        self.clock = clock
        self.session = requests.Session()
        retries = Retry(total=retry_count, backoff_factor=retry_backoff_factor, status_forcelist=sorted(self.RETRY_STATUSES))
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def fetch(self, dataset_id: str, start: datetime, stop: datetime) -> FetchResult[pd.DataFrame]:
        """Fetches the shortest file of a dataset that reaches back to the start, and keeps its rows inside the range.

        Args:
            dataset_id: The dataset ID, a key of SWPC_PRODUCTS.
            start: The start of the range (inclusive), timezone-aware.
            stop: The end of the range (exclusive), timezone-aware.

        Returns:
            One row per time with a UTC DatetimeIndex; a result without data if the file holds no row in the range.

        Raises:
            ValueError: If SWPC_PRODUCTS has no dataset of this ID.
            SourceUnreachableError: If SWPC sent no answer.
            DataRetrievalError: If the download failed.
            DataProcessingError: If the file is no list of rows with the expected fields.
        """
        product = SWPC_PRODUCTS.get(dataset_id)
        if product is None:
            raise ValueError(f"Unknown SWPC dataset '{dataset_id}'; known are {sorted(SWPC_PRODUCTS)}.")
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

        in_range = frame[(frame.index >= start) & (frame.index < stop)]
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
    for length, path in product.files:
        if now - length <= start + FILE_START_TOLERANCE:
            return path
    return product.files[-1][1]
