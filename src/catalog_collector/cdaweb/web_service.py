from typing import Any

import requests

from space_weather_analysis.util.error_handling import DataProcessingError, DataRetrievalError
from space_weather_analysis.util.resilience import RETRY_AFTER_STATUSES, retry_after_seconds


def get_json(session: requests.Session, url: str, timeout: tuple[float, float], what: str) -> Any:
    """Downloads one response of the CDAWeb Web Services (https://cdaweb.gsfc.nasa.gov/WebServices/) and parses it as JSON.

    Args:
        session: The HTTP session for the request.
        url: The location of the response.
        timeout: The connect and read timeout in seconds.
        what: What the response is, named in the error message.

    Returns:
        The parsed JSON content.

    Raises:
        DataRetrievalError: If the download fails or the status is not 2xx; carries the Retry-After wait of HTTP 429 and 503.
        DataProcessingError: If the content is no JSON.
    """
    try:
        response = session.get(url, headers={"Accept": "application/json"}, timeout=timeout)
    except requests.RequestException as e:
        raise DataRetrievalError(f"{what} {url} could not be downloaded: {e}") from e
    if not response.ok:
        retry_after = retry_after_seconds(response.headers.get("Retry-After")) if response.status_code in RETRY_AFTER_STATUSES else None
        raise DataRetrievalError(f"{what} {url} answered HTTP {response.status_code}", retry_after)
    try:
        return response.json()
    except ValueError as e:
        raise DataProcessingError(f"{what} {url} is no JSON: {e}") from e
