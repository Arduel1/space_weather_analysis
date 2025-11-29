import logging
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DataRetrievalError(Exception):
    """
    Raised when data cannot be retrieved from any available source.

    This exception indicates that all fallback mechanisms have been exhausted
    and the requested data is unavailable.
    """

    pass


class DataProcessingError(Exception):
    """
    Raised when data processing fails after successful retrieval.

    This exception indicates an issue with data transformation, validation,
    or storage rather than retrieval.
    """

    pass


class CatalogError(Exception):
    """
    Raised when catalog operations fail.

    This exception indicates issues with catalog discovery, building,
    or querying operations.
    """

    pass


def log_and_handle_expected_failure(
    operation_name: str, context: str, error: Exception, logger_instance: logging.Logger
) -> None:
    """
    Logs an expected operational failure at the appropriate level.

    Expected failures are normal operational conditions such as server
    timeouts during fallback attempts or missing optional data sources.
    These are logged at INFO or WARNING level depending on context.

    Args:
        operation_name: Name of the operation that failed.
        context: Additional context about the failure.
        error: The exception that occurred.
        logger_instance: Logger instance to use for output.
    """
    logger_instance.info(f"{operation_name} failed as expected during {context}: {error}")


def log_and_handle_unexpected_failure(
    operation_name: str, context: str, error: Exception, logger_instance: logging.Logger
) -> None:
    """
    Logs an unexpected failure at ERROR level with full stack trace.

    Unexpected failures indicate genuine errors that require investigation,
    such as programming errors, data corruption, or infrastructure failures.

    Args:
        operation_name: Name of the operation that failed.
        context: Additional context about the failure.
        error: The exception that occurred.
        logger_instance: Logger instance to use for output.
    """
    logger_instance.error(f"{operation_name} failed unexpectedly during {context}: {error}", exc_info=True)


def safe_execute_with_fallback(
    primary_operation: Callable[[], T],
    fallback_operation: Optional[Callable[[], T]],
    operation_name: str,
    logger_instance: logging.Logger,
) -> Optional[T]:
    """
    Executes an operation with optional fallback handling.

    This function provides a standardized pattern for operations that may
    have fallback alternatives. It distinguishes between expected failures
    during primary operations and genuine errors.

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
        if fallback_operation is not None:
            log_and_handle_expected_failure(operation_name, "primary attempt", primary_error, logger_instance)
            try:
                return fallback_operation()
            except Exception as fallback_error:
                log_and_handle_unexpected_failure(operation_name, "fallback attempt", fallback_error, logger_instance)
                return None
        else:
            log_and_handle_unexpected_failure(
                operation_name, "execution (no fallback available)", primary_error, logger_instance
            )
            return None
