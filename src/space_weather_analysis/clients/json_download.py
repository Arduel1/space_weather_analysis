import json
import tempfile
from http import HTTPStatus
from typing import Any

import requests

from space_weather_analysis.util.error_handling import DataProcessingError, DataRetrievalError, ResourceNotFoundError
from space_weather_analysis.util.resilience import RETRY_AFTER_STATUSES, retry_after_seconds

# Bytes written to the temporary file per step of a streamed download.
DOWNLOAD_CHUNK_BYTES = 1024 * 1024


def download_json(session: requests.Session, url: str, timeout: tuple[float, float], what: str) -> Any:
    """Downloads one JSON document of a provider web service and parses it.

    Args:
        session: The HTTP session for the request.
        url: The location of the document.
        timeout: The connect and read timeout in seconds.
        what: What the document is, named in the error messages.

    Returns:
        The parsed JSON content.

    Raises:
        ResourceNotFoundError: If the provider answers that the document does not exist (HTTP 404).
        DataRetrievalError: If the download fails or the status is no other 2xx; carries the Retry-After wait of HTTP 429 and 503.
        DataProcessingError: If the content is no JSON.
    """
    try:
        response = session.get(url, headers={"Accept": "application/json"}, timeout=timeout)
    except requests.RequestException as e:
        raise DataRetrievalError(f"{what} {url} could not be downloaded: {e}") from e
    _raise_for_status(response, url, what)
    try:
        return response.json()
    except ValueError as e:
        raise DataProcessingError(f"{what} {url} is no JSON: {e}") from e


def download_json_streamed(session: requests.Session, url: str, timeout: tuple[float, float], what: str) -> Any:
    """Downloads one large JSON document through a temporary file and parses it.

    The response body goes to the file in chunks, so it is not held in memory beside the parsed content.

    Args:
        session: The HTTP session for the request.
        url: The location of the document.
        timeout: The connect timeout and the longest wait for the next chunk in seconds.
        what: What the document is, named in the error messages.

    Returns:
        The parsed JSON content.

    Raises:
        ResourceNotFoundError: If the provider answers that the document does not exist (HTTP 404).
        DataRetrievalError: If the download fails or the status is no other 2xx; carries the Retry-After wait of HTTP 429 and 503.
        DataProcessingError: If the content is no JSON.
    """
    with tempfile.TemporaryFile() as download:
        try:
            with session.get(url, timeout=timeout, stream=True) as response:
                _raise_for_status(response, url, what)
                for chunk in response.iter_content(DOWNLOAD_CHUNK_BYTES):
                    download.write(chunk)
        except requests.RequestException as e:
            raise DataRetrievalError(f"{what} {url} could not be downloaded: {e}") from e
        download.seek(0)
        try:
            return json.load(download)
        except ValueError as e:
            raise DataProcessingError(f"{what} {url} is no JSON: {e}") from e


def _raise_for_status(response: requests.Response, url: str, what: str) -> None:
    """Raises the error matching an unsuccessful HTTP status of a provider response.

    Args:
        response: The response.
        url: The location requested.
        what: What the document is, named in the error messages.

    Raises:
        ResourceNotFoundError: If the status is 404.
        DataRetrievalError: If the status is no other 2xx; carries the Retry-After wait of HTTP 429 and 503.
    """
    if response.status_code == HTTPStatus.NOT_FOUND:
        raise ResourceNotFoundError(f"{what} {url} does not exist")
    if not response.ok:
        retry_after = retry_after_seconds(response.headers.get("Retry-After")) if response.status_code in RETRY_AFTER_STATUSES else None
        raise DataRetrievalError(f"{what} {url} answered HTTP {response.status_code}", retry_after)
