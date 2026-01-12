import logging
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SpaceWeatherError(Exception):
    """
    Base class for all exceptions specific to the Space Weather Analysis application.
    Allows catching all app-related errors with a single except block.
    """

    pass


class DataRetrievalError(SpaceWeatherError):
    """
    Raised when data cannot be retrieved from any available source.
    """

    pass


class DataProcessingError(SpaceWeatherError):
    """
    Raised when data processing fails after successful retrieval.
    """

    pass


class CatalogError(SpaceWeatherError):
    """
    Raised when catalog operations fail.
    """

    pass


def log_and_handle_expected_failure(
    operation_name: str,
    context: str,
    error: Exception,
    logger_instance: logging.Logger,
) -> None:
    """
    Logs an expected operational failure at the INFO/WARNING level.

    Used for non-critical failures like server timeouts during fallback attempts.

    Args:
        operation_name: Name of the operation that failed.
        context: Additional context about the failure.
        error: The exception that occurred.
        logger_instance: Logger instance to use for output.
    """
    logger_instance.info(f"{operation_name} failed as expected during {context}: {error}")


def log_and_handle_unexpected_failure(
    operation_name: str,
    context: str,
    error: Exception,
    logger_instance: logging.Logger,
) -> None:
    """
    Logs an unexpected failure at ERROR level with full stack trace.

    Args:
        operation_name: Name of the operation that failed.
        context: Additional context about the failure.
        error: The exception that occurred.
        logger_instance: Logger instance to use for output.
    """
    logger_instance.error(
        f"{operation_name} failed unexpectedly during {context}: {error}",
        exc_info=True,
    )


def safe_execute_with_fallback(
    primary_operation: Callable[[], T],
    fallback_operation: Optional[Callable[[], T]],
    operation_name: str,
    logger_instance: logging.Logger,
) -> Optional[T]:
    """
    Executes an operation with optional fallback handling.

    Args:
        primary_operation: The primary operation to attempt.
        fallback_operation: Optional fallback to attempt on failure.
        operation_name: Descriptive name for logging.
        logger_instance: Logger instance to use.

    Returns:
        The result from either primary or fallback operation, or None if both fail.
    """
    try:
        return primary_operation()
    except Exception as primary_error:
        if fallback_operation is None:
            log_and_handle_unexpected_failure(
                operation_name,
                "execution (no fallback available)",
                primary_error,
                logger_instance,
            )
            return None

        log_and_handle_expected_failure(operation_name, "primary attempt", primary_error, logger_instance)

        try:
            return fallback_operation()
        except Exception as fallback_error:
            log_and_handle_unexpected_failure(operation_name, "fallback attempt", fallback_error, logger_instance)
            return None
