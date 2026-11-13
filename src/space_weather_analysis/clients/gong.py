import logging
import pathlib
import re
from datetime import UTC, datetime, timedelta

import requests

from space_weather_analysis.clients.download import download_file, download_text
from space_weather_analysis.util.error_handling import DataProcessingError, ResourceNotFoundError

logger = logging.getLogger(__name__)

# The zero-point corrected synoptic magnetograms of the Global Oscillation Network Group (GONG) of the NSF National
# Solar Observatory: a map of the radial magnetic field of the whole solar surface, updated hourly, one directory per
# month and per day
GONG_SYNOPTIC_URL = "https://gong2.nso.edu/oQR/zqs/"
GONG_PAGE_URL = "https://gong.nso.edu/data/magmap/"

# The link to a map file in a directory listing, such as href="mrzqs261108t2014c2317_082.fits.gz": group 1 the file
# name, group 2 the date as yymmdd after the product code mrzqs, group 3 the time as HHMM after "t", then "c" with the
# Carrington rotation and the Carrington longitude after "_", which are not used; "\." is a literal dot
MAP_FILE_PATTERN = re.compile(r'href="(mrzqs(\d{6})t(\d{4})c\d+_\d+\.fits\.gz)"')
# Connect and read timeout of a directory listing in seconds
DIRECTORY_TIMEOUT = (10.0, 60.0)
# Connect timeout and longest wait for the next chunk of a map download in seconds
MAP_TIMEOUT = (10.0, 120.0)


def _listing(session: requests.Session, url: str) -> str:
    """Reads a directory listing of the GONG server; an empty text for a missing directory.

    Raises:
        SourceUnreachableError: If the server sent no answer.
        DataRetrievalError: If the listing cannot be downloaded.
    """
    try:
        return download_text(session, url, DIRECTORY_TIMEOUT, "GONG directory")
    except ResourceNotFoundError:
        return ""


def recent_maps(session: requests.Session, now: datetime, span: timedelta) -> list[tuple[str, datetime]]:
    """Lists the synoptic maps of a span ending now, newest first.

    Args:
        session: The HTTP session for the requests.
        now: The current time.
        span: The span searched.

    Returns:
        The URL and time of each map.

    Raises:
        DataRetrievalError: If a directory cannot be read.
    """
    maps = []
    # The span reaches into this many day directories, today's included
    days = int(span / timedelta(days=1)) + 1
    for days_back in range(days):
        day = (now - timedelta(days=days_back)).astimezone(UTC)
        directory = f"{GONG_SYNOPTIC_URL}{day:%Y%m}/mrzqs{day:%y%m%d}/"
        for name, date, clock in MAP_FILE_PATTERN.findall(_listing(session, directory)):
            time = datetime.strptime(date + clock, "%y%m%d%H%M").replace(tzinfo=UTC)
            if time >= now - span:
                maps.append((directory + name, time))
    # A map linked twice in a listing counts once
    return sorted(set(maps), key=lambda entry: entry[1], reverse=True)


def download_map(session: requests.Session, url: str, directory: pathlib.Path) -> pathlib.Path:
    """Downloads a map file into a directory unless it is there already.

    Args:
        session: The HTTP session for the request.
        url: The URL of the map file.
        directory: The directory the map file is stored in.

    Returns:
        The path of the map file.

    Raises:
        DataRetrievalError: If the download fails.
        DataProcessingError: If the URL names no map file.
    """
    name = url.rsplit("/", 1)[-1]
    if not name.endswith(".fits.gz"):
        raise DataProcessingError(f"{url} names no GONG map file.")
    path = directory / name
    if not path.exists():
        download_file(session, url, path, MAP_TIMEOUT, "GONG synoptic map")
        logger.info("Downloaded the GONG synoptic map %s", name)
    return path
