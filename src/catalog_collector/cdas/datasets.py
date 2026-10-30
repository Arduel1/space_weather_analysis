import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional

import requests

from catalog_collector.scheduling.worker import TaskFailedError
from space_weather_analysis.metadata.overlays import cdas_time_interval

logger = logging.getLogger(__name__)

# CDAS, the CDAWeb Coordinated Data Analysis System (https://cdaweb.gsfc.nasa.gov/WebServices/REST/), lists every
# CDAWeb dataset with its time range, DOI, SPASE resource ID and notes page in one response of about 3 MB.
CDAS_DATASETS_URL = "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys/datasets"

# Connect and read timeout in seconds.
CDAS_TIMEOUT = (10, 120)

# CDAS answers these statuses when it is overloaded and names the wait in a Retry-After header.
RETRY_AFTER_STATUSES = (429, 503)


@dataclass(frozen=True)
class CdasDataset:
    """One dataset entry of the CDAS dataset list.

    Attributes:
        dataset_id: The CDAWeb dataset ID.
        start: The start time text as given by CDAS; None if the entry has no parsable time range.
        stop: The stop time text as given by CDAS; None if the entry has no parsable time range.
        start_time: The parsed start time in UTC.
        stop_time: The parsed stop time in UTC.
        entry: The entry as listed by CDAS.
    """

    dataset_id: str
    start: Optional[str]
    stop: Optional[str]
    start_time: Optional[datetime]
    stop_time: Optional[datetime]
    entry: Dict[str, Any]


class CdasRequestError(TaskFailedError):
    """Raised when the CDAS dataset list cannot be downloaded or parsed."""


def fetch_cdas_datasets(session: requests.Session, url: str = CDAS_DATASETS_URL) -> Dict[str, CdasDataset]:
    """Downloads and parses the CDAS dataset list.

    Args:
        session: The HTTP session for the request.
        url: The location of the dataset list.

    Returns:
        The entries keyed by dataset ID; entries without ID are left out.

    Raises:
        CdasRequestError: If the download fails or the response is no dataset list; carries the Retry-After wait of HTTP 429 and 503.
    """
    try:
        response = session.get(url, headers={"Accept": "application/json"}, timeout=CDAS_TIMEOUT)
    except requests.RequestException as e:
        raise CdasRequestError(f"CDAS dataset list {url} could not be downloaded: {e}") from e
    if not response.ok:
        retry_after = retry_after_seconds(response.headers.get("Retry-After")) if response.status_code in RETRY_AFTER_STATUSES else None
        raise CdasRequestError(f"CDAS dataset list {url} answered HTTP {response.status_code}", retry_after)

    try:
        entries = response.json()["DatasetDescription"]
    except (ValueError, KeyError, TypeError) as e:
        raise CdasRequestError(f"CDAS dataset list {url} is no dataset list: {e}") from e
    if not isinstance(entries, list):
        raise CdasRequestError(f"CDAS dataset list {url} holds no list of datasets")

    datasets: Dict[str, CdasDataset] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("Id"), str):
            continue
        time_interval = cdas_time_interval(entry)
        if time_interval is None:
            datasets[entry["Id"]] = CdasDataset(entry["Id"], None, None, None, None, entry)
        else:
            datasets[entry["Id"]] = CdasDataset(entry["Id"], *time_interval, entry)
    logger.info(f"CDAS lists {len(datasets)} datasets.")
    return datasets


def retry_after_seconds(header: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    """Reads the wait of a Retry-After header, given as seconds or as HTTP date.

    Args:
        header: The header value.
        now: The current time in UTC; the current system time if None.

    Returns:
        The wait in seconds, at least 0, or None if the header is missing or not readable.
    """
    if not header:
        return None
    text = header.strip()
    if text.isdigit():
        return float(text)
    try:
        retry_at = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max((retry_at - (now or datetime.now(timezone.utc))).total_seconds(), 0.0)
