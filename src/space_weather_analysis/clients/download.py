import json
import pathlib
import tempfile
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from typing import Any

import requests

from space_weather_analysis.util.atomic_files import atomic_file
from space_weather_analysis.util.error_handling import DataProcessingError, DataRetrievalError, ResourceNotFoundError, SourceUnreachableError
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
        SourceUnreachableError: If the provider sent no answer.
        DataRetrievalError: If the download fails or the status is no other 2xx; carries the Retry-After wait of HTTP 429 and 503.
        DataProcessingError: If the content is no JSON.
    """
    try:
        response = session.get(url, headers={"Accept": "application/json"}, timeout=timeout)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise SourceUnreachableError(f"{what} {url} is unreachable: {e}") from e
    except requests.RequestException as e:
        raise DataRetrievalError(f"{what} {url} could not be downloaded: {e}") from e
    _raise_for_status(response, url, what)
    try:
        return response.json()
    except ValueError as e:
        raise DataProcessingError(f"{what} {url} is no JSON: {e}") from e


def download_text(session: requests.Session, url: str, timeout: tuple[float, float], what: str) -> str:
    """Downloads one text document of a provider web server.

    Args:
        session: The HTTP session for the request.
        url: The location of the document.
        timeout: The connect and read timeout in seconds.
        what: What the document is, named in the error messages.

    Returns:
        The text of the document.

    Raises:
        ResourceNotFoundError: If the provider answers that the document does not exist (HTTP 404).
        SourceUnreachableError: If the provider sent no answer.
        DataRetrievalError: If the download fails or the status is no other 2xx; carries the Retry-After wait of HTTP 429 and 503.
    """
    try:
        response = session.get(url, timeout=timeout)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise SourceUnreachableError(f"{what} {url} is unreachable: {e}") from e
    except requests.RequestException as e:
        raise DataRetrievalError(f"{what} {url} could not be downloaded: {e}") from e
    _raise_for_status(response, url, what)
    return response.text


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
        SourceUnreachableError: If the provider sent no answer.
        DataRetrievalError: If the download fails or the status is no other 2xx; carries the Retry-After wait of HTTP 429 and 503.
        DataProcessingError: If the content is no JSON.
    """
    with tempfile.TemporaryFile() as download:
        try:
            with session.get(url, timeout=timeout, stream=True) as response:
                _raise_for_status(response, url, what)
                for chunk in response.iter_content(DOWNLOAD_CHUNK_BYTES):
                    download.write(chunk)
        except (requests.ConnectionError, requests.Timeout) as e:
            raise SourceUnreachableError(f"{what} {url} is unreachable: {e}") from e
        except requests.RequestException as e:
            raise DataRetrievalError(f"{what} {url} could not be downloaded: {e}") from e
        download.seek(0)
        try:
            return json.load(download)
        except ValueError as e:
            raise DataProcessingError(f"{what} {url} is no JSON: {e}") from e


def last_modified(session: requests.Session, url: str, timeout: tuple[float, float], what: str) -> datetime:
    """Reads when a provider file last changed from the Last-Modified header of a HEAD request, without downloading it.

    Args:
        session: The HTTP session for the request.
        url: The location of the file.
        timeout: The connect and read timeout in seconds.
        what: What the file is, named in the error messages.

    Returns:
        The time of the last change, timezone-aware in UTC.

    Raises:
        ResourceNotFoundError: If the provider answers that the file does not exist (HTTP 404).
        SourceUnreachableError: If the provider sent no answer.
        DataRetrievalError: If the request fails, the status is no other 2xx or the answer names no time of change.
    """
    try:
        response = session.head(url, timeout=timeout, allow_redirects=True)
    except (requests.ConnectionError, requests.Timeout) as e:
        raise SourceUnreachableError(f"{what} {url} is unreachable: {e}") from e
    except requests.RequestException as e:
        raise DataRetrievalError(f"{what} {url} could not be checked: {e}") from e
    _raise_for_status(response, url, what)
    header = response.headers.get("Last-Modified")
    if header is None:
        raise DataRetrievalError(f"{what} {url} names no time of its last change")
    # The header is an HTTP date such as "Fri, 13 Nov 2026 17:49:04 GMT", which always stands for UTC
    try:
        return parsedate_to_datetime(header).astimezone(UTC)
    except (TypeError, ValueError) as e:
        raise DataRetrievalError(f"{what} {url} names an unreadable time of change {header!r}") from e


def download_file(session: requests.Session, url: str, target: pathlib.Path, timeout: tuple[float, float], what: str) -> None:
    """Downloads one file of a provider and replaces the target file with it only once it is complete.

    Args:
        session: The HTTP session for the request.
        url: The location of the file.
        target: The path the file is stored at.
        timeout: The connect timeout and the longest wait for the next chunk in seconds.
        what: What the file is, named in the error messages.

    Raises:
        ResourceNotFoundError: If the provider answers that the file does not exist (HTTP 404).
        SourceUnreachableError: If the provider sent no answer.
        DataRetrievalError: If the download fails or the status is no other 2xx; carries the Retry-After wait of HTTP 429 and 503.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    # A partial download stays in a temporary file next to the target, so a failure keeps the previous file
    with atomic_file(target) as download:
        try:
            with session.get(url, timeout=timeout, stream=True) as response:
                _raise_for_status(response, url, what)
                for chunk in response.iter_content(DOWNLOAD_CHUNK_BYTES):
                    download.write(chunk)
        except (requests.ConnectionError, requests.Timeout) as e:
            raise SourceUnreachableError(f"{what} {url} is unreachable: {e}") from e
        except requests.RequestException as e:
            raise DataRetrievalError(f"{what} {url} could not be downloaded: {e}") from e


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
