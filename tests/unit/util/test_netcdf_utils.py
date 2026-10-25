from datetime import datetime
from typing import Any, Dict, Generator, Tuple
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import xarray as xr

from space_weather_analysis.util.netcdf_utils import (
    TimeConfidence,
    _evaluate_variable_confidence,
    _is_datetime_type,
    _read_variable_sample,
    calendar_is_present,
    fallback_decode_time,
    find_heuristic_time_candidates,
)


class TestFindHeuristicTimeCandidates:
    """Selection of numeric variables along the time dimension."""

    def test_finds_numeric_1d_variable(self) -> None:
        """Every 1D numeric variable along the time dimension is a candidate."""
        ds = xr.Dataset(
            data_vars={
                "Epoch": (("time",), np.arange(100, dtype=float)),
                "B_Field": (("time",), np.random.rand(100)),
            },
            coords={"time": np.arange(100)},
        )

        candidates = find_heuristic_time_candidates(ds, "time")

        assert "Epoch" in candidates
        assert "B_Field" in candidates

    def test_ignores_non_numeric(self) -> None:
        """A string variable along the time dimension is not a candidate."""
        ds = xr.Dataset(data_vars={"TimeLabels": (("time",), np.array(["t1", "t2"], dtype="<U2")), "TimeNumeric": (("time",), np.array([1.0, 2.0]))})
        candidates = find_heuristic_time_candidates(ds, "time")

        assert "TimeNumeric" in candidates
        assert "TimeLabels" not in candidates

    def test_ignores_wrong_dimension(self) -> None:
        """A numeric variable on another dimension is not a candidate."""
        ds = xr.Dataset(data_vars={"Frequency": (("freq",), np.arange(10)), "Time": (("time",), np.arange(10))})
        candidates = find_heuristic_time_candidates(ds, "time")

        assert "Time" in candidates
        assert "Frequency" not in candidates


class TestFallbackDecodeTime:
    """Candidate selection and decoding in fallback_decode_time."""

    @pytest.fixture
    def mock_attempt_conversion(self) -> Generator[MagicMock, None, None]:
        """Patches attempt_numeric_time_conversions in netcdf_utils."""
        with patch("space_weather_analysis.util.netcdf_utils.attempt_numeric_time_conversions") as mock:
            yield mock

    def test_decodes_full_array_correctly(self, mock_attempt_conversion: MagicMock) -> None:
        """A large variable is probed on a 1000-value sample, then decoded in full size."""
        # Above the sample threshold of 50000 values.
        full_size = 60000
        raw_data = np.arange(full_size, dtype=float)
        ds = xr.Dataset(data_vars={"Epoch": (("time",), raw_data)})

        def side_effect(arr: np.ndarray, *args: Any, **kwargs: Any) -> Tuple[np.ndarray, Dict[str, Any]]:
            decoded = np.array([datetime(2000, 1, 1)] * len(arr))
            diag = {"accepted": True, "selected_transform": "unix", "selected_stats": {}}
            return decoded, diag

        mock_attempt_conversion.side_effect = side_effect

        result, diag = fallback_decode_time(ds, "time")

        assert result is not None
        assert len(result) == full_size, "Result must match full dataset size, not sample size!"
        assert diag["selected_variable"] == "Epoch"

        assert mock_attempt_conversion.call_count == 2

        first_call_args = mock_attempt_conversion.call_args_list[0][0][0]
        assert len(first_call_args) == 1000

        second_call_args = mock_attempt_conversion.call_args_list[1][0][0]
        assert len(second_call_args) == full_size

    def test_fails_gracefully_if_no_candidates(self, mock_attempt_conversion: MagicMock) -> None:
        """Without numeric candidates, returns None with reason 'no_candidates'."""
        ds = xr.Dataset(data_vars={"StrVar": (("time",), ["a", "b"])})

        result, diag = fallback_decode_time(ds, "time")

        assert result is None
        assert diag["reason"] == "no_candidates"
        mock_attempt_conversion.assert_not_called()

    def test_fails_if_conversion_rejected(self, mock_attempt_conversion: MagicMock) -> None:
        """A rejected conversion returns None with reason 'no_plausible_decoding'."""
        ds = xr.Dataset(data_vars={"Noise": (("time",), np.random.rand(100))})

        mock_attempt_conversion.return_value = (None, {"accepted": False})

        result, diag = fallback_decode_time(ds, "time")

        assert result is None
        assert diag["reason"] == "no_plausible_decoding"

    def test_respects_allow_attempt_flag(self) -> None:
        """allow_attempt=False returns None immediately."""
        ds = xr.Dataset(data_vars={"Epoch": (("time",), [1, 2])})
        result, diag = fallback_decode_time(ds, "time", allow_attempt=False)

        assert result is None
        assert diag["reason"] == "allow_attempt=False"


class TestReadVariableSample:
    """Sampling of large variables in _read_variable_sample."""

    def test_reads_full_small_array(self) -> None:
        """An array of at most 50000 values is read completely."""
        data = np.arange(100)
        da = xr.DataArray(data, dims="time")

        sample = _read_variable_sample(da, "time")

        assert len(sample) == 100
        np.testing.assert_array_equal(sample, data)

    def test_slices_large_array_along_time_dim(self) -> None:
        """A larger array is cut to its first 1000 values along the time dimension."""
        data = np.zeros((60000,))
        da = xr.DataArray(data, dims="time")

        sample = _read_variable_sample(da, "time")

        assert len(sample) == 1000
        assert sample.ndim == 1

    def test_handles_multi_dim_slicing(self) -> None:
        """A large 2D array yields the first 1000 time steps at index 0 of the other dimension."""
        data = np.zeros((2000, 100))
        da = xr.DataArray(data, dims=("time", "height"))

        sample = _read_variable_sample(da, "time")

        assert len(sample) == 1000


class TestIsDatetimeType:
    def test_identifies_datetime64(self) -> None:
        """A datetime64 dtype is a datetime type."""
        da = xr.DataArray(np.array(["2020-01-01"], dtype="datetime64[ns]"))
        assert _is_datetime_type(da) is True

    def test_rejects_float(self) -> None:
        """A float dtype is not a datetime type."""
        da = xr.DataArray(np.array([1.0, 2.0]))
        assert _is_datetime_type(da) is False

    def test_handles_mock_objects_gracefully(self) -> None:
        """An object without dtype attribute is not a datetime type."""
        mock_obj = MagicMock()
        del mock_obj.dtype
        assert _is_datetime_type(mock_obj) is False  # type: ignore


class TestEvaluateVariableConfidence:
    def test_certain_if_units_since(self) -> None:
        """Units of the form '<unit> since <epoch>' give CERTAIN."""
        da = xr.DataArray([], attrs={"units": "seconds since 1970-01-01"})
        score = _evaluate_variable_confidence("Time", da)
        assert score == TimeConfidence.CERTAIN

    def test_high_if_axis_t(self) -> None:
        """axis='T' gives HIGH."""
        da = xr.DataArray([], attrs={"axis": "T"})
        score = _evaluate_variable_confidence("random_name", da)
        assert score == TimeConfidence.HIGH

    def test_high_if_standard_name_time(self) -> None:
        """standard_name='time' gives HIGH."""
        da = xr.DataArray([], attrs={"standard_name": "time"})
        score = _evaluate_variable_confidence("random_name", da)
        assert score == TimeConfidence.HIGH

    def test_low_if_name_matches_heuristic(self) -> None:
        """The name 'Epoch' without attributes gives LOW."""
        da = xr.DataArray([])
        score = _evaluate_variable_confidence("Epoch", da)
        assert score == TimeConfidence.LOW

    def test_none_if_no_match(self) -> None:
        """Neither a time attribute nor a time-like name gives NONE."""
        da = xr.DataArray([], attrs={"units": "nT"})
        score = _evaluate_variable_confidence("B_Field", da)
        assert score == TimeConfidence.NONE


class TestCalendarIsPresent:
    def test_returns_true_if_key_exists(self) -> None:
        """Attributes with a 'calendar' key give True."""
        assert calendar_is_present({"calendar": "standard"}) is True

    def test_returns_false_if_key_missing(self) -> None:
        """Attributes without a 'calendar' key give False."""
        assert calendar_is_present({"units": "m"}) is False
