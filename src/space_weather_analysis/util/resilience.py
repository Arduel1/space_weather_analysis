import functools
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import sleep
from typing import Any, TypeVar, cast

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# HTTP statuses with which an overloaded server names the wait before the next request in a Retry-After header.
RETRY_AFTER_STATUSES = frozenset({429, 503})


def retry_interaction(*, exceptions: tuple[type[BaseException], ...], max_retries: int = 3, backoff_factor: float = 1.0) -> Callable[[F], F]:
    """Builds a decorator that calls a function again when it raises one of the given exceptions.

    Only the named exceptions are retried, so programming errors fail at once. Each retry waits
    backoff_factor * 2 ** (retry_number - 1) seconds; the last failure is raised to the caller.

    Args:
        exceptions: The exception types that mark a failure a repeated call may not have.
        max_retries: The number of calls in total, at least 1.
        backoff_factor: The wait before the first retry in seconds, doubled for every further retry.

    Returns:
        The decorator.

    Raises:
        ValueError: If max_retries is below 1.
    """
    if max_retries < 1:
        raise ValueError(f"max_retries must be at least 1, got {max_retries}")

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(1, max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as error:
                    sleep_seconds = backoff_factor * (2 ** (attempt - 1))
                    logger.warning("Attempt %d/%d of %s failed: %s; retrying in %.2f s", attempt, max_retries, func.__name__, error, sleep_seconds)
                    sleep(sleep_seconds)
            return func(*args, **kwargs)

        return cast(F, wrapper)

    return decorator


def retry_after_seconds(header: str | None, now: datetime | None = None) -> float | None:
    """Reads the wait of a Retry-After header, given as seconds or as HTTP date.

    Args:
        header: The header value.
        now: The current time; the current system time if None.

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
