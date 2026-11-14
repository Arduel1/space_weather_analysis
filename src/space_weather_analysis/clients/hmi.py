import logging
import pathlib
from datetime import datetime

import requests
from sunpy.coordinates import sun

from space_weather_analysis.clients.download import download_file, last_modified
from space_weather_analysis.util.error_handling import ResourceNotFoundError

logger = logging.getLogger(__name__)

# The synoptic magnetograms of the Helioseismic and Magnetic Imager (HMI) on NASA's Solar Dynamics Observatory, served
# as static files by the Joint Science Operations Center (JSOC) of Stanford University, one file per Carrington rotation
HMI_SYNOPTIC_URL = "https://jsoc1.stanford.edu/data/hmi/synoptic/"
# The near-real-time map of a rotation, 720 by 360 cells, updated about daily while the rotation runs
CURRENT_MAP_FILE = "hmi.Synoptic_Mr_small_nrt.{rotation}.fits"
# The final map of a completed rotation with the poles filled in, 3600 by 1440 cells, published days after its end
FINAL_MAP_FILE = "hmi.Synoptic_Mr_polfil.{rotation}.fits"
# Connect and read timeout of the HEAD request that tells whether a map changed, in seconds
CHECK_TIMEOUT = (10.0, 30.0)
# Connect timeout and longest wait for the next chunk of a map download in seconds
MAP_TIMEOUT = (10.0, 120.0)


def running_rotation(now: datetime) -> int:
    """Returns the number of the Carrington rotation running at a time, the solar rotation count used by synoptic maps."""
    return int(sun.carrington_rotation_number(now))


def latest_maps(session: requests.Session, now: datetime, directory: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, datetime]:
    """Downloads the newest near-real-time map and the final map of the rotation before it, unless they are stored.

    A new rotation has no near-real-time map in its first days, and a completed rotation no final map in the days after
    its end; both cases take the map of the rotation before.

    Args:
        session: The HTTP session for the requests.
        now: The current time.
        directory: The directory the maps are kept in.

    Returns:
        The near-real-time map, the final map of an earlier rotation and when the near-real-time map was last updated.

    Raises:
        SourceUnreachableError: If JSOC sent no answer.
        DataRetrievalError: If no map of the rotations tried exists or a download fails.
    """
    rotation = running_rotation(now)
    current_rotation, updated = _newest(session, CURRENT_MAP_FILE, (rotation, rotation - 1))
    # The update time is part of the stored name, so a map that changed is downloaded again and an unchanged one is not
    current = _download(session, CURRENT_MAP_FILE.format(rotation=current_rotation), directory, f"{updated:%Y%m%dT%H%M%SZ}")
    final_rotation, _ = _newest(session, FINAL_MAP_FILE, (current_rotation - 1, current_rotation - 2))
    # A final map never changes after its publication, so its name alone identifies it
    final = _download(session, FINAL_MAP_FILE.format(rotation=final_rotation), directory, "")
    return current, final, updated


def _newest(session: requests.Session, pattern: str, rotations: tuple[int, ...]) -> tuple[int, datetime]:
    """Finds the first of the rotations, in the order given, that has a map file, and when that file last changed.

    Raises:
        SourceUnreachableError: If JSOC sent no answer.
        DataRetrievalError: If none of the rotations has a map file.
    """
    for rotation in rotations:
        try:
            return rotation, last_modified(session, HMI_SYNOPTIC_URL + pattern.format(rotation=rotation), CHECK_TIMEOUT, "HMI synoptic map")
        except ResourceNotFoundError:
            continue
    raise ResourceNotFoundError(f"No HMI synoptic map {pattern} of the rotations {rotations}.")


def _download(session: requests.Session, name: str, directory: pathlib.Path, version: str) -> pathlib.Path:
    """Downloads a map file into the directory under its name with an optional version, unless that file is stored.

    Raises:
        SourceUnreachableError: If JSOC sent no answer.
        DataRetrievalError: If the download fails.
    """
    path = directory / (name.replace(".fits", f".{version}.fits") if version else name)
    if not path.exists():
        download_file(session, HMI_SYNOPTIC_URL + name, path, MAP_TIMEOUT, "HMI synoptic map")
        logger.info("Downloaded the HMI synoptic map %s", path.name)
    return path
