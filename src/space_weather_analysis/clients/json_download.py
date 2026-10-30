from http import HTTPStatus
from typing import Any

import requests

from space_weather_analysis.util.error_handling import DataProcessingError, DataRetrievalError, ResourceNotFoundError
from space_weather_analysis.util.resilience import RETRY_AFTER_STATUSES, retry_after_seconds


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
    if response.status_code == HTTPStatus.NOT_FOUND:
        raise ResourceNotFoundError(f"{what} {url} does not exist")
    if not response.ok:
        retry_after = retry_after_seconds(response.headers.get("Retry-After")) if response.status_code in RETRY_AFTER_STATUSES else None
        raise DataRetrievalError(f"{what} {url} answered HTTP {response.status_code}", retry_after)
    try:
        return response.json()
    except ValueError as e:
        raise DataProcessingError(f"{what} {url} is no JSON: {e}") from e
