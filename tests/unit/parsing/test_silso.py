import pathlib

import pandas as pd
import pytest

from pipeline_service.parsing.silso import parse_silso_daily, parse_silso_monthly
from space_weather_analysis.util.error_handling import DataProcessingError

# Days in synthetic_sunspots.csv (invented values, SILSO layout): a definitive day, a day without a number (-1), a provisional day with 0.
SYNTHETIC_DATA_DIR = pathlib.Path(__file__).parents[2] / "integration" / "pipeline" / "data"
SYNTHETIC_FILE = SYNTHETIC_DATA_DIR / "synthetic_sunspots.csv"
# Months in synthetic_sunspots_monthly.csv (invented values, SILSO layout): definitive, without a value (-1), provisional.
SYNTHETIC_MONTHLY_FILE = SYNTHETIC_DATA_DIR / "synthetic_sunspots_monthly.csv"
# Months in synthetic_sunspots_smoothed.csv (invented values, SILSO layout): definitive, provisional, not yet smoothed (-1).
SYNTHETIC_SMOOTHED_FILE = SYNTHETIC_DATA_DIR / "synthetic_sunspots_smoothed.csv"
MONTHS = pd.DatetimeIndex(["2001-03-01", "2001-04-01", "2001-05-01"], tz="UTC", name="timestamp")
DAYS = pd.DatetimeIndex(["2001-03-14", "2001-03-15", "2001-03-16"], tz="UTC", name="timestamp")


def _write_rows(tmp_path: pathlib.Path, *rows: str) -> pathlib.Path:
    """Writes the given rows after the rows of the synthetic file and returns the path of the new file."""
    path = tmp_path / "sunspots.csv"
    path.write_text(SYNTHETIC_FILE.read_text() + "".join(f"{row}\n" for row in rows))
    return path


def test_silso_days_parsed_with_missing_number_as_na_and_provisional_flag() -> None:
    """Each day becomes a row at its UTC date; -1 leaves number, deviation and observations NA; indicator 0 means provisional."""
    frame = parse_silso_daily(SYNTHETIC_FILE)

    expected = pd.DataFrame(
        {
            "sunspot_number": pd.array([123, pd.NA, 0], dtype="Int64"),
            "std_dev": [11.5, float("nan"), 0.0],
            "num_observations": pd.array([27, pd.NA, 25], dtype="Int64"),
            "is_provisional": [False, False, True],
            "decimal_year": [2001.199, 2001.201, 2001.204],
        },
        index=DAYS,
    )
    pd.testing.assert_frame_equal(frame, expected)


def test_silso_invalid_value_is_na_in_its_field_and_invalid_date_row_skipped(tmp_path: pathlib.Path) -> None:
    """A non-numeric sunspot number leaves only that field NA; a row with month 13 is skipped."""
    path = _write_rows(tmp_path, "2001;03;17;2001.207;  abc; 3.0;  12;1", "2001;13;01;2001.900;  10; 1.0;   5;1")

    frame = parse_silso_daily(path)

    assert list(frame.index) == [*DAYS, pd.Timestamp("2001-03-17", tz="UTC")]
    assert frame.iloc[-1].dropna().to_dict() == {
        "std_dev": 3.0,
        "num_observations": 12,
        "is_provisional": False,
        "decimal_year": 2001.207,
    }


def test_silso_missing_file_and_file_without_valid_row_are_rejected(tmp_path: pathlib.Path) -> None:
    """A missing file raises FileNotFoundError; a file without a valid date raises DataProcessingError."""
    with pytest.raises(FileNotFoundError):
        parse_silso_daily(tmp_path / "missing.csv")

    invalid_only = tmp_path / "invalid.csv"
    invalid_only.write_text("2001;13;01;2001.900;  10; 1.0;   5;1\n")
    with pytest.raises(DataProcessingError):
        parse_silso_daily(invalid_only)


@pytest.mark.parametrize(
    ("path", "sunspot_number", "std_dev", "is_provisional"),
    [
        pytest.param(SYNTHETIC_MONTHLY_FILE, [123.4, float("nan"), 95.0], [11.5, float("nan"), 9.8], [False, False, True], id="monthly_mean"),
        pytest.param(SYNTHETIC_SMOOTHED_FILE, [118.2, 114.9, float("nan")], [6.1, 5.9, float("nan")], [False, True, True], id="smoothed"),
    ],
)
def test_silso_months_parsed_at_month_start_with_missing_value_as_na(
    path: pathlib.Path, sunspot_number: list[float], std_dev: list[float], is_provisional: list[bool]
) -> None:
    """Each month becomes a row at its first day 00:00 UTC; -1 leaves number and deviation NA and keeps the observations."""
    frame = parse_silso_monthly(path)

    expected = pd.DataFrame(
        {
            "sunspot_number": sunspot_number,
            "std_dev": std_dev,
            "num_observations": pd.array([827, 760, 801], dtype="Int64"),
            "is_provisional": is_provisional,
            "decimal_year": [2001.204, 2001.288, 2001.371],
        },
        index=MONTHS,
    )
    pd.testing.assert_frame_equal(frame, expected)


def test_silso_monthly_missing_file_and_file_without_valid_row_are_rejected(tmp_path: pathlib.Path) -> None:
    """A missing file raises FileNotFoundError; a file without a valid month raises DataProcessingError."""
    with pytest.raises(FileNotFoundError):
        parse_silso_monthly(tmp_path / "missing.csv")

    invalid_only = tmp_path / "invalid.csv"
    invalid_only.write_text("2001;13;2001.950;  10.0; 1.0;   5;1\n")
    with pytest.raises(DataProcessingError):
        parse_silso_monthly(invalid_only)
