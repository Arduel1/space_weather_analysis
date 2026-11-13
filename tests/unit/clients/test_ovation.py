from datetime import UTC, datetime

import pytest

from space_weather_analysis.clients.ovation import parse_grid
from space_weather_analysis.util.error_handling import DataProcessingError


def test_ovation_document_becomes_cells_with_longitudes_from_minus_180() -> None:
    """The rows of the document keep their order, longitudes above 179 degrees turn negative, both times are read."""
    document = {"Observation Time": "2026-11-09T20:52:00Z", "Forecast Time": "2026-11-09T21:33:00Z", "coordinates": [[0, 60, 5], [270, 65, 30]]}

    grid = parse_grid(document)

    assert (grid.forecast_time, grid.longitude.tolist(), grid.probability.tolist()) == (
        datetime(2026, 11, 9, 21, 33, tzinfo=UTC),
        [0.0, -90.0],
        [5.0, 30.0],
    )


def test_ovation_document_without_times_is_rejected() -> None:
    """A document without forecast time cannot be placed in time."""
    with pytest.raises(DataProcessingError):
        parse_grid({"coordinates": [[0, 60, 5]]})
