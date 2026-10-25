import functools
import logging
import time
from typing import Any, Callable, TypeVar, cast

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def retry_interaction(max_retries: int = 3, backoff_factor: float = 1.0, exceptions: tuple = (Exception,)) -> Callable[[F], F]:
    """Decorator to retry a function call upon encountering specific exceptions.

    This ensures robustness for external API calls by
    automatically retrying failed attempts with an exponential backoff strategy.

    Args:
        max_retries (int): Maximum number of retry attempts. Defaults to 3.
        backoff_factor (float): Multiplier for sleep time between retries.
                                Sleep = backoff_factor * (2 ** (retry_number - 1)).
        exceptions (tuple): A tuple of exception classes to catch and retry.
                            Defaults to catching all generic Exceptions.

    Returns:
        Callable: The decorated function with retry logic.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 0
            while attempt < max_retries:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    attempt += 1
                    if attempt >= max_retries:
                        logger.error(f"Function '{func.__name__}' failed after {max_retries} attempts. Last error: {e}", exc_info=True)
                        raise  # Re-raise the last exception to let the caller handle it

                    sleep_time = backoff_factor * (2 ** (attempt - 1))
                    logger.warning(f"Attempt {attempt}/{max_retries} for '{func.__name__}' failed: {e}. Retrying in {sleep_time:.2f}s...")
                    time.sleep(sleep_time)
            return None  # Should be unreachable due to raise

        return cast(F, wrapper)

    return decorator
