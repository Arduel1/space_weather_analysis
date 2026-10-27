import numpy as np
import xarray as xr

from pipeline_service.parsing.file_parser import _assign_decoded_times


def test_assign_decoded_times_returns_dataset_with_time_coordinate() -> None:
    """The returned dataset holds the decoded times as coordinate along the time dimension; the given dataset stays unchanged."""
    dataset = xr.Dataset({"flux": (("record",), np.array([1.0, 2.0, 3.0]))})
    decoded_times = np.array(["2024-01-01T00:00", "2024-01-01T00:01", "2024-01-01T00:02"], dtype="datetime64[ns]")

    result = _assign_decoded_times(dataset, "record", "Epoch_fallback", decoded_times)

    assert result["Epoch_fallback"].dims == ("record",)
    np.testing.assert_array_equal(result["Epoch_fallback"].values, decoded_times)
    assert "Epoch_fallback" not in dataset.coords
