from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from catalog_collector.scheduling.models import TaskFailedError

# CDAWeb answers these statuses when it is overloaded and names the wait in a Retry-After header.
RETRY_AFTER_STATUSES = (429, 503)


class CdawebRequestError(TaskFailedError):
    """Raised when a CDAWeb web service response cannot be downloaded or parsed."""


def get_json(session: requests.Session, url: str, timeout: tuple[float, float], what: str) -> Any:
    """Downloads one CDAWeb response and parses it as JSON.

    Args:
        session: The HTTP session for the request.
        url: The location of the response.
        timeout: The connect and read timeout in seconds.
        what: What the response is, named in the error message.

    Returns:
        The parsed JSON content.

    Raises:
        CdawebRequestError: If the download fails, the status is not 2xx or the content is no JSON; carries the Retry-After wait of HTTP 429 and 503.
    """
    try:
        response = session.get(url, headers={"Accept": "application/json"}, timeout=timeout)
    except requests.RequestException as e:
        raise CdawebRequestError(f"{what} {url} could not be downloaded: {e}") from e
    if not response.ok:
        retry_after = retry_after_seconds(response.headers.get("Retry-After")) if response.status_code in RETRY_AFTER_STATUSES else None
        raise CdawebRequestError(f"{what} {url} answered HTTP {response.status_code}", retry_after)
    try:
        return response.json()
    except ValueError as e:
        raise CdawebRequestError(f"{what} {url} is no JSON: {e}") from e


def retry_after_seconds(header: str | None, now: datetime | None = None) -> float | None:
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
        retry_at = retry_at.replace(tzinfo=UTC)
    return max((retry_at - (now or datetime.now(UTC))).total_seconds(), 0.0)
