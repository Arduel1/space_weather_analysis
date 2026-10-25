import logging
from unittest.mock import MagicMock

import pytest

from space_weather_analysis.util.error_handling import (
    log_and_handle_expected_failure,
    log_and_handle_unexpected_failure,
    safe_execute_with_fallback,
)


class TestErrorLoggingWrappers:
    def test_log_expected_failure(self) -> None:
        """An expected failure is logged once at INFO with operation, context and error."""
        mock_logger = MagicMock(spec=logging.Logger)

        op_name = "Download"
        context = "retry attempt 1"
        error = ValueError("Connection reset")

        log_and_handle_expected_failure(op_name, context, error, mock_logger)

        mock_logger.info.assert_called_once()
        args, _ = mock_logger.info.call_args
        log_message = args[0]

        assert "Download" in log_message
        assert "retry attempt 1" in log_message
        assert "Connection reset" in log_message
        assert "failed as expected" in log_message

    def test_log_unexpected_failure(self) -> None:
        """An unexpected failure is logged once at ERROR with stack trace."""
        mock_logger = MagicMock(spec=logging.Logger)

        op_name = "Parsing"
        context = "main loop"
        error = KeyError("missing_id")

        log_and_handle_unexpected_failure(op_name, context, error, mock_logger)

        mock_logger.error.assert_called_once()

        args, kwargs = mock_logger.error.call_args
        log_message = args[0]

        assert "Parsing" in log_message
        assert "main loop" in log_message
        assert "missing_id" in log_message
        assert "failed unexpectedly" in log_message
        assert kwargs.get("exc_info") is True


class TestSafeExecuteWithFallback:
    @pytest.fixture
    def mock_logger(self) -> MagicMock:
        return MagicMock(spec=logging.Logger)

    def test_primary_success(self, mock_logger: MagicMock) -> None:
        """Returns the primary result without calling the fallback or logging."""
        primary = MagicMock(return_value="Success")
        fallback = MagicMock()

        result = safe_execute_with_fallback(primary, fallback, "TestOp", mock_logger)

        assert result == "Success"
        primary.assert_called_once()
        fallback.assert_not_called()
        mock_logger.info.assert_not_called()
        mock_logger.error.assert_not_called()

    def test_primary_fail_fallback_success(self, mock_logger: MagicMock) -> None:
        """Returns the fallback result and logs the primary failure at INFO."""
        primary_err = ValueError("Primary failed")
        primary = MagicMock(side_effect=primary_err)
        fallback = MagicMock(return_value="Fallback Success")

        result = safe_execute_with_fallback(primary, fallback, "TestOp", mock_logger)

        assert result == "Fallback Success"
        primary.assert_called_once()
        fallback.assert_called_once()

        mock_logger.info.assert_called_once()
        assert "primary attempt" in mock_logger.info.call_args[0][0]
        mock_logger.error.assert_not_called()

    def test_primary_fail_fallback_fail(self, mock_logger: MagicMock) -> None:
        """Returns None, logs the primary failure at INFO and the fallback failure at ERROR."""
        primary = MagicMock(side_effect=ValueError("Primary failed"))
        fallback_err = RuntimeError("Fallback failed")
        fallback = MagicMock(side_effect=fallback_err)

        result = safe_execute_with_fallback(primary, fallback, "TestOp", mock_logger)

        assert result is None

        mock_logger.info.assert_called_once()
        assert "primary attempt" in mock_logger.info.call_args[0][0]
        mock_logger.error.assert_called_once()
        assert "fallback attempt" in mock_logger.error.call_args[0][0]

    def test_primary_fail_no_fallback(self, mock_logger: MagicMock) -> None:
        """Without fallback, returns None and logs the primary failure at ERROR."""
        primary = MagicMock(side_effect=ValueError("Fatal error"))

        result = safe_execute_with_fallback(primary, None, "TestOp", mock_logger)

        assert result is None

        mock_logger.info.assert_not_called()
        mock_logger.error.assert_called_once()
        assert "no fallback available" in mock_logger.error.call_args[0][0]
