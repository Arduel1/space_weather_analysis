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
        """
        Verify that expected failures are logged at INFO level with the correct format.
        """
        # Mock the logger
        mock_logger = MagicMock(spec=logging.Logger)

        # Test inputs
        op_name = "Download"
        context = "retry attempt 1"
        error = ValueError("Connection reset")

        # Execute
        log_and_handle_expected_failure(op_name, context, error, mock_logger)

        # Assert
        mock_logger.info.assert_called_once()
        # Verify the formatting logic indirectly by checking the call args
        args, _ = mock_logger.info.call_args
        log_message = args[0]

        assert "Download" in log_message
        assert "retry attempt 1" in log_message
        assert "Connection reset" in log_message
        assert "failed as expected" in log_message

    def test_log_unexpected_failure(self) -> None:
        """
        Verify that unexpected failures are logged at ERROR level with stack traces.
        """
        mock_logger = MagicMock(spec=logging.Logger)

        op_name = "Parsing"
        context = "main loop"
        error = KeyError("missing_id")

        # Execute
        log_and_handle_unexpected_failure(op_name, context, error, mock_logger)

        # Assert
        mock_logger.error.assert_called_once()

        # Verify arguments and keyword arguments (exc_info=True is crucial here)
        args, kwargs = mock_logger.error.call_args
        log_message = args[0]

        assert "Parsing" in log_message
        assert "main loop" in log_message
        assert "missing_id" in log_message
        assert "failed unexpectedly" in log_message

        # Ensure stack trace logging is enabled
        assert kwargs.get("exc_info") is True


class TestSafeExecuteWithFallback:
    @pytest.fixture
    def mock_logger(self) -> MagicMock:
        return MagicMock(spec=logging.Logger)

    def test_primary_success(self, mock_logger: MagicMock) -> None:
        """
        Scenario: Primary operation succeeds.
        Expected: Returns result, fallback not called, no logging.
        """
        primary = MagicMock(return_value="Success")
        fallback = MagicMock()

        result = safe_execute_with_fallback(primary, fallback, "TestOp", mock_logger)

        assert result == "Success"
        primary.assert_called_once()
        fallback.assert_not_called()
        mock_logger.info.assert_not_called()
        mock_logger.error.assert_not_called()

    def test_primary_fail_fallback_success(self, mock_logger: MagicMock) -> None:
        """
        Scenario: Primary fails, fallback succeeds.
        Expected: Returns fallback result, logs primary failure as INFO.
        """
        primary_err = ValueError("Primary failed")
        primary = MagicMock(side_effect=primary_err)
        fallback = MagicMock(return_value="Fallback Success")

        result = safe_execute_with_fallback(primary, fallback, "TestOp", mock_logger)

        assert result == "Fallback Success"
        primary.assert_called_once()
        fallback.assert_called_once()

        # Check that primary failure was logged as INFO (expected failure)
        mock_logger.info.assert_called_once()
        assert "primary attempt" in mock_logger.info.call_args[0][0]
        mock_logger.error.assert_not_called()

    def test_primary_fail_fallback_fail(self, mock_logger: MagicMock) -> None:
        """
        Scenario: Both primary and fallback fail.
        Expected: Returns None, logs primary as INFO, logs fallback as ERROR.
        """
        primary = MagicMock(side_effect=ValueError("Primary failed"))
        fallback_err = RuntimeError("Fallback failed")
        fallback = MagicMock(side_effect=fallback_err)

        result = safe_execute_with_fallback(primary, fallback, "TestOp", mock_logger)

        assert result is None

        # Primary failure -> INFO
        mock_logger.info.assert_called_once()
        assert "primary attempt" in mock_logger.info.call_args[0][0]

        # Fallback failure -> ERROR
        mock_logger.error.assert_called_once()
        assert "fallback attempt" in mock_logger.error.call_args[0][0]

    def test_primary_fail_no_fallback(self, mock_logger: MagicMock) -> None:
        """
        Scenario: Primary fails, no fallback provided.
        Expected: Returns None, logs primary failure as ERROR.
        """
        primary = MagicMock(side_effect=ValueError("Fatal error"))

        result = safe_execute_with_fallback(primary, None, "TestOp", mock_logger)

        assert result is None

        # No fallback -> ERROR immediately
        mock_logger.info.assert_not_called()
        mock_logger.error.assert_called_once()
        assert "no fallback available" in mock_logger.error.call_args[0][0]
