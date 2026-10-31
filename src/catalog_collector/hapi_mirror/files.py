from typing import Any

import requests

from space_weather_analysis.clients.json_download import download_json, download_json_streamed

# The HAPI project (Heliophysics Application Programmer's Interface, https://hapi-server.org/) runs a mirror
# that fetches the /catalog and /info responses of all known HAPI servers every night and publishes them as files.
MIRROR_BASE_URL = "https://hapi-server.org/meta/"

# Per server: its base URL in about.x_url and the dataset IDs of its /catalog response.
MIRROR_CATALOGS_PATH = "catalogs.json"

# One row per dataset of every server, with the startDate and stopDate of its /info response; about 25 MB.
MIRROR_DATASET_TABLE_PATH = "table/hapi.all.datasets.json"

# The /info responses of all datasets of every mirrored server in one file of about 300 MB.
MIRROR_INFOS_PATH = "catalogs-all.json"

# Connect timeout and the longest wait for the next part of a response in seconds; the largest file takes minutes.
MIRROR_TIMEOUT = (10.0, 300.0)


def mirror_file_url(mirror_base_url: str, path: str) -> str:
    """Builds the location of one mirror file.

    Args:
        mirror_base_url: The base URL of the mirror files.
        path: The path of the file below the base URL.

    Returns:
        The URL of the file.
    """
    return f"{mirror_base_url.rstrip('/')}/{path}"


def fetch_mirror_json(session: requests.Session, mirror_base_url: str, path: str) -> Any:
    """Downloads one mirror file and parses it as JSON.

    Args:
        session: The HTTP session for the request.
        mirror_base_url: The base URL of the mirror files.
        path: The path of the file below the base URL.

    Returns:
        The parsed JSON content.

    Raises:
        DataRetrievalError: If the download fails; carries the Retry-After wait of HTTP 429 and 503.
        DataProcessingError: If the content is no JSON.
    """
    return download_json(session, mirror_file_url(mirror_base_url, path), MIRROR_TIMEOUT, "HAPI mirror file")


def fetch_mirror_json_streamed(session: requests.Session, mirror_base_url: str, path: str) -> Any:
    """Downloads one large mirror file through a temporary file and parses it as JSON.

    Args:
        session: The HTTP session for the request.
        mirror_base_url: The base URL of the mirror files.
        path: The path of the file below the base URL.

    Returns:
        The parsed JSON content.

    Raises:
        DataRetrievalError: If the download fails; carries the Retry-After wait of HTTP 429 and 503.
        DataProcessingError: If the content is no JSON.
    """
    return download_json_streamed(session, mirror_file_url(mirror_base_url, path), MIRROR_TIMEOUT, "HAPI mirror file")
