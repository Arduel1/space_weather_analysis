import pathlib

import pandas as pd
import pytest

from pipeline_service.parsing.cme_catalog import parse_cme_catalog
from space_weather_analysis.util.error_handling import DataProcessingError

# Events in synthetic_cme_catalog.txt (invented values, SOHO/LASCO CME catalog layout): a regular, a halo and a placeholder event.
SYNTHETIC_FILE = pathlib.Path(__file__).parents[2] / "integration" / "pipeline" / "data" / "synthetic_cme_catalog.txt"
EVENT_TIMES = pd.DatetimeIndex(["2003-05-10 04:12:30", "2003-05-11 17:45:06", "2003-05-12 09:03:51"], tz="UTC", name="timestamp")
# The 4 header lines of the catalog file.
HEADER = "".join(SYNTHETIC_FILE.read_text().splitlines(keepends=True)[:4])


def _event(frame: pd.DataFrame, position: int) -> dict[str, object]:
    """Returns the fields of the event at the given position without its NA values."""
    return frame.iloc[position].dropna().to_dict()


def test_cme_events_parsed_with_halo_markers_and_placeholders() -> None:
    """Halo sets is_halo and leaves central_pa NA; '*' sets the flag of its column; dash placeholders become NA."""
    frame = parse_cme_catalog(SYNTHETIC_FILE)

    assert frame.index.equals(EVENT_TIMES)
    assert _event(frame, 0) == {
        "central_pa": 211,
        "width": 34,
        "linear_speed": 412,
        "speed_initial": 430,
        "speed_final": 395,
        "speed_at_20r": 0,
        "accel": -9.8,
        "mass": 2.4e15,
        "kinetic_energy": 6.2e29,
        "mpa": 207,
        "is_halo": False,
        "accel_unreliable": True,
        "remarks": "Poor Event;  Only C3; Partial Halo",
    }
    halo_event = _event(frame, 1)
    assert halo_event["is_halo"] is True
    assert "central_pa" not in halo_event
    assert halo_event["mass_uncertain"] is True
    assert halo_event["kinetic_energy_uncertain"] is True
    assert "accel_unreliable" not in halo_event
    assert _event(frame, 2) == {
        "central_pa": 58,
        "width": 21,
        "mass": 1.7e14,
        "mpa": 61,
        "is_halo": False,
        "mass_uncertain": True,
        "remarks": "Very Poor Event",
    }


def test_cme_invalid_number_is_na_in_its_field_and_invalid_date_row_skipped(tmp_path: pathlib.Path) -> None:
    """A non-numeric width leaves only that field NA; a row with month 13 is skipped."""
    path = tmp_path / "cme.txt"
    path.write_text(
        HEADER
        + "2003/05/13  11:30:00    120    abc    300     290    310    320      1.5    1.0e+15    4.0e+29    118   Poor Event\n"
        + "2003/13/01  06:00:00    120     45    300     290    310    320      1.5    1.0e+15    4.0e+29    118   Poor Event\n"
    )

    frame = parse_cme_catalog(path)

    assert list(frame.index) == [pd.Timestamp("2003-05-13 11:30:00", tz="UTC")]
    event = _event(frame, 0)
    assert "width" not in event
    assert event["linear_speed"] == 300


def test_cme_missing_file_and_file_without_valid_row_are_rejected(tmp_path: pathlib.Path) -> None:
    """A missing file raises FileNotFoundError; a file with only the header raises DataProcessingError."""
    with pytest.raises(FileNotFoundError):
        parse_cme_catalog(tmp_path / "missing.txt")

    header_only = tmp_path / "header.txt"
    header_only.write_text(HEADER)
    with pytest.raises(DataProcessingError):
        parse_cme_catalog(header_only)
