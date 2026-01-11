from datetime import datetime
from typing import Any, Dict, Generator, Tuple
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import xarray as xr

from common.util.netcdf_utils import (
    TimeConfidence,
    _evaluate_variable_confidence,
    _is_datetime_type,
    _read_variable_sample,
    calendar_is_present,
    fallback_decode_time,
    find_heuristic_time_candidates,
)


class TestFindHeuristicTimeCandidates:
    """
    Tests the logic for identifying potential time variables in a Dataset.
    """

    def test_finds_numeric_1d_variable(self) -> None:
        """
        Scenario: Dataset has a clear 1D numeric 'time' variable.
        Expected: It is identified and returned as the top candidate.
        """
        ds = xr.Dataset(
            data_vars={
                "Epoch": (("time",), np.arange(100, dtype=float)),
                "B_Field": (("time",), np.random.rand(100)),
            },
            coords={"time": np.arange(100)},
        )

        # 'Epoch' is 1D, numeric, and shares the time dimension.
        candidates = find_heuristic_time_candidates(ds, "time")

        assert "Epoch" in candidates
        assert "B_Field" in candidates
        # Epoch should logically be high priority due to name heuristics
        # (if heuristic name matching is active via _evaluate_variable_confidence)

    def test_ignores_non_numeric(self) -> None:
        """
        Scenario: Variable has time dim but is string type.
        Expected: Ignored because we can't do numeric conversion on it.
        """
        ds = xr.Dataset(data_vars={"TimeLabels": (("time",), np.array(["t1", "t2"], dtype="<U2")), "TimeNumeric": (("time",), np.array([1.0, 2.0]))})
        candidates = find_heuristic_time_candidates(ds, "time")

        assert "TimeNumeric" in candidates
        assert "TimeLabels" not in candidates

    def test_ignores_wrong_dimension(self) -> None:
        """
        Scenario: Variable is numeric but does not use the time dimension.
        Expected: Ignored.
        """
        ds = xr.Dataset(data_vars={"Frequency": (("freq",), np.arange(10)), "Time": (("time",), np.arange(10))})
        candidates = find_heuristic_time_candidates(ds, "time")

        assert "Time" in candidates
        assert "Frequency" not in candidates


class TestFallbackDecodeTime:
    """
    Tests the 'fallback_decode_time' orchestration logic.
    """

    @pytest.fixture
    def mock_attempt_conversion(self) -> Generator[MagicMock, None, None]:
        """Mocks the time_utils conversion function to control success/failure."""
        with patch("space_weather_analysis.util.netcdf_utils.attempt_numeric_time_conversions") as mock:
            yield mock

    def test_decodes_full_array_correctly(self, mock_attempt_conversion: MagicMock) -> None:
        """
        Scenario: A large dataset (size > 50000) is successfully identified via a sample (size 1000).
        Expected:
            1. attempt_numeric_time_conversions is called TWICE (Sample + Full).
            2. The returned array has the FULL size, ensuring no shape mismatch downstream.
        """
        # Setup: Create a dataset larger than the sample threshold (50,000)
        full_size = 60000
        raw_data = np.arange(full_size, dtype=float)
        ds = xr.Dataset(data_vars={"Epoch": (("time",), raw_data)})

        # Mock behavior: Return a dummy decoded array of the SAME size as input array passed to it
        def side_effect(arr: np.ndarray, *args: Any, **kwargs: Any) -> Tuple[np.ndarray, Dict[str, Any]]:
            # Simulate success: Return an array of datetimes matching input length
            decoded = np.array([datetime(2000, 1, 1)] * len(arr))
            diag = {"accepted": True, "selected_transform": "unix", "selected_stats": {}}
            return decoded, diag

        mock_attempt_conversion.side_effect = side_effect

        # Action
        result, diag = fallback_decode_time(ds, "time")

        # Assertions
        assert result is not None
        assert len(result) == full_size, "Result must match full dataset size, not sample size!"
        assert diag["selected_variable"] == "Epoch"

        # Verify calls
        assert mock_attempt_conversion.call_count == 2

        # Check first call (Sample)
        first_call_args = mock_attempt_conversion.call_args_list[0][0][0]
        assert len(first_call_args) == 1000  # Sample size limit from _read_variable_sample

        # Check second call (Full Data)
        second_call_args = mock_attempt_conversion.call_args_list[1][0][0]
        assert len(second_call_args) == full_size  # Full size

    def test_fails_gracefully_if_no_candidates(self, mock_attempt_conversion: MagicMock) -> None:
        """
        Scenario: Dataset has no valid numeric candidates.
        Expected: Returns None with reason 'no_candidates'.
        """
        ds = xr.Dataset(data_vars={"StrVar": (("time",), ["a", "b"])})  # String only

        result, diag = fallback_decode_time(ds, "time")

        assert result is None
        assert diag["reason"] == "no_candidates"
        mock_attempt_conversion.assert_not_called()

    def test_fails_if_conversion_rejected(self, mock_attempt_conversion: MagicMock) -> None:
        """
        Scenario: Candidate exists, but conversion returns accepted=False (not plausible).
        Expected: Returns None with reason 'no_plausible_decoding'.
        """
        ds = xr.Dataset(data_vars={"Noise": (("time",), np.random.rand(100))})

        # Mock failure response
        mock_attempt_conversion.return_value = (None, {"accepted": False})

        result, diag = fallback_decode_time(ds, "time")

        assert result is None
        assert diag["reason"] == "no_plausible_decoding"

    def test_respects_allow_attempt_flag(self) -> None:
        """
        Scenario: Called with allow_attempt=False.
        Expected: Immediate exit without processing.
        """
        ds = xr.Dataset(data_vars={"Epoch": (("time",), [1, 2])})
        result, diag = fallback_decode_time(ds, "time", allow_attempt=False)

        assert result is None
        assert diag["reason"] == "allow_attempt=False"


class TestReadVariableSample:
    """
    Tests the internal helper _read_variable_sample.
    """

    def test_reads_full_small_array(self) -> None:
        """
        Scenario: Array is small (< 50000).
        Expected: Reads entire array flattened.
        """
        data = np.arange(100)
        da = xr.DataArray(data, dims="time")

        sample = _read_variable_sample(da, "time")

        assert len(sample) == 100
        np.testing.assert_array_equal(sample, data)

    def test_slices_large_array_along_time_dim(self) -> None:
        """
        Scenario: Array is large (> 50000).
        Expected: Slices only the first 1000 elements along the time dimension.
        """
        # Create a large array (60,000 elements)
        data = np.zeros((60000,))
        da = xr.DataArray(data, dims="time")

        sample = _read_variable_sample(da, "time")

        assert len(sample) == 1000  # Should be capped at 1000
        assert sample.ndim == 1  # Should be flattened

    def test_handles_multi_dim_slicing(self) -> None:
        """
        Scenario: 2D array (Time x Height).
        Expected: Slices time=0..1000, and takes index 0 for other dimensions to get 1D sample.
        """
        # Time=2000, Height=100 -> Total 200,000 (Large)
        data = np.zeros((2000, 100))
        da = xr.DataArray(data, dims=("time", "height"))

        sample = _read_variable_sample(da, "time")

        # Logic: isel(time=slice(0, 1000), height=0) -> shape (1000,)
        assert len(sample) == 1000


class TestIsDatetimeType:
    def test_identifies_datetime64(self) -> None:
        """Scenario: DataArray has datetime64 dtype."""
        da = xr.DataArray(np.array(["2020-01-01"], dtype="datetime64[ns]"))
        assert _is_datetime_type(da) is True

    def test_rejects_float(self) -> None:
        """Scenario: DataArray has float dtype."""
        da = xr.DataArray(np.array([1.0, 2.0]))
        assert _is_datetime_type(da) is False

    def test_handles_mock_objects_gracefully(self) -> None:
        """Scenario: Input has no dtype attribute (e.g. mocked bad input)."""
        mock_obj = MagicMock()
        del mock_obj.dtype  # Simulate missing attribute
        assert _is_datetime_type(mock_obj) is False  # type: ignore


class TestEvaluateVariableConfidence:
    def test_certain_if_units_since(self) -> None:
        """Scenario: Attributes contain 'seconds since 1970'."""
        da = xr.DataArray([], attrs={"units": "seconds since 1970-01-01"})
        score = _evaluate_variable_confidence("Time", da)
        assert score == TimeConfidence.CERTAIN

    def test_high_if_axis_t(self) -> None:
        """Scenario: Attributes contain axis='T'."""
        da = xr.DataArray([], attrs={"axis": "T"})
        score = _evaluate_variable_confidence("random_name", da)
        assert score == TimeConfidence.HIGH

    def test_high_if_standard_name_time(self) -> None:
        """Scenario: Attributes contain standard_name='time'."""
        da = xr.DataArray([], attrs={"standard_name": "time"})
        score = _evaluate_variable_confidence("random_name", da)
        assert score == TimeConfidence.HIGH

    def test_low_if_name_matches_heuristic(self) -> None:
        """Scenario: No attributes, but name is 'Epoch'."""
        da = xr.DataArray([])
        score = _evaluate_variable_confidence("Epoch", da)
        assert score == TimeConfidence.LOW

    def test_none_if_no_match(self) -> None:
        """Scenario: Random variable with no time clues."""
        da = xr.DataArray([], attrs={"units": "nT"})
        score = _evaluate_variable_confidence("B_Field", da)
        assert score == TimeConfidence.NONE


class TestCalendarIsPresent:
    def test_returns_true_if_key_exists(self) -> None:
        assert calendar_is_present({"calendar": "standard"}) is True

    def test_returns_false_if_key_missing(self) -> None:
        assert calendar_is_present({"units": "m"}) is False
