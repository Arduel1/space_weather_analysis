import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import requests

from space_weather_analysis.clients.download import download_json
from space_weather_analysis.util.error_handling import DataProcessingError

logger = logging.getLogger(__name__)

# The Heliophysics Event Knowledgebase (HEK, Hurlburt et al. 2012, Solar Physics 275, 67) of the Lockheed Martin Solar
# and Astrophysics Laboratory collects solar events, among them the daily sunspot regions of the NOAA SWPC report
HEK_SEARCH_URL = "https://www.lmsal.com/hek/her"
HEK_PAGE_URL = "https://www.lmsal.com/hek/"

# The reporter of the NOAA sunspot regions, one entry per region and day
NOAA_REGION_REPORTER = "NOAA SWPC Observer"

# Rows of one answer; a longer result comes in further pages
HEK_PAGE_SIZE = 1000

# Connect and read timeout in seconds
HEK_TIMEOUT = (10.0, 90.0)

# HEK attributes of a region entry, mapped to the stored field names
REGION_ATTRIBUTES = {
    "ar_noaanum": "region",
    "hgs_y": "latitude",
    "hgs_x": "longitude",
    "area_atdiskcenter": "area_km2",
    "ar_mcintoshcls": "spot_class",
    "ar_mtwilsoncls": "mag_class",
}


def region_query(start: datetime, stop: datetime, page: int) -> str:
    """Builds the HEK search for the NOAA region entries of a time range.

    Args:
        start: The start of the range.
        stop: The end of the range.
        page: The page of the result, from 1.

    Returns:
        The search URL.
    """
    parameters = {
        # Answer in JSON
        "cosec": 2,
        "cmd": "search",
        "type": "column",
        "event_type": "ar",
        "event_starttime": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        "event_endtime": stop.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
        # The search needs a region of the disk; this one holds the whole Sun in arcseconds
        "event_coordsys": "helioprojective",
        "x1": -1200,
        "x2": 1200,
        "y1": -1200,
        "y2": 1200,
        "param0": "frm_name",
        "op0": "=",
        "value0": NOAA_REGION_REPORTER,
        "result_limit": HEK_PAGE_SIZE,
        "page": page,
        "return": ",".join(["event_starttime", *REGION_ATTRIBUTES]),
    }
    return f"{HEK_SEARCH_URL}?{urlencode(parameters)}"


def parse_regions(answer: Any) -> pd.DataFrame:
    """Reads the region entries of a HEK answer, one row per region and day at the start of the day.

    An entry without valid time or region number is left out.

    Args:
        answer: The parsed JSON answer of the HEK search.

    Returns:
        The entries with a UTC DatetimeIndex and the stored fields of REGION_ATTRIBUTES.

    Raises:
        DataProcessingError: If the answer holds no result list.
    """
    rows = answer.get("result") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        raise DataProcessingError("HEK answer holds no result list.")
    # Fixed columns, so that an empty answer and an entry lacking an attribute give the same table
    frame = pd.DataFrame(rows, columns=["event_starttime", *REGION_ATTRIBUTES])
    times = pd.to_datetime(frame["event_starttime"], utc=True, errors="coerce")
    regions = pd.to_numeric(frame["ar_noaanum"], errors="coerce")
    valid = times.notna() & regions.notna()
    stored = frame.loc[valid, list(REGION_ATTRIBUTES)].rename(columns=REGION_ATTRIBUTES)
    # Classes the reporter left empty are missing values
    stored = stored.replace("", None).set_index(pd.DatetimeIndex(times[valid]))
    stored.index.name = None
    return stored.sort_index()


def fetch_regions(session: requests.Session, start: datetime, stop: datetime) -> pd.DataFrame:
    """Fetches the NOAA region entries of a time range from the HEK, page by page.

    Args:
        session: The HTTP session for the requests.
        start: The start of the range.
        stop: The end of the range.

    Returns:
        The entries inside the range.

    Raises:
        DataRetrievalError: If a request failed.
        SourceUnreachableError: If the HEK sent no answer.
        DataProcessingError: If an answer cannot be read.
    """
    pages = []
    page = 1
    while True:
        answer = download_json(session, region_query(start, stop, page), HEK_TIMEOUT, "HEK region search")
        rows = parse_regions(answer)
        pages.append(rows)
        # A page shorter than HEK_PAGE_SIZE is the last; the raw result counts, as parsing may drop entries
        if len(answer.get("result", [])) < HEK_PAGE_SIZE:
            break
        page += 1
    regions = pd.concat(pages) if len(pages) > 1 else pages[0]
    # The HEK also returns entries that only overlap the range
    regions = regions[(regions.index >= start) & (regions.index < stop)]
    logger.info("Fetched %d HEK region entries from %s to %s", len(regions), start, stop)
    return regions
