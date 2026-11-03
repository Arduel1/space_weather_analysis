import logging
from pathlib import Path

import pandas as pd

from space_weather_analysis.util.error_handling import DataProcessingError

logger = logging.getLogger(__name__)

# Identifies the source and version of the SILSO (Sunspot Index and Long-term Solar Observations) data.
SILSO_PROVIDER_ID = "SILSO_V2.0"

# Columns after the date columns, the same in the daily and the monthly files.
_MEASUREMENT_COLUMNS = ("decimal_year", "sunspot_number", "std_dev", "num_observations", "indicator")
# Column order of the daily total sunspot number file.
_DAILY_COLUMNS = ("year", "month", "day", *_MEASUREMENT_COLUMNS)
# Column order of the monthly mean and the 13-month smoothed total sunspot number files.
_MONTHLY_COLUMNS = ("year", "month", *_MEASUREMENT_COLUMNS)
# The placeholder SILSO writes for a value it does not have.
_MISSING_VALUE = -1
# Fields that hold placeholders: in the daily file all of them on days without a sunspot number, in the
# monthly files each one on its own where it is -1.
_PLACEHOLDER_FIELDS = ("sunspot_number", "std_dev", "num_observations")


def parse_silso_daily(path: Path) -> pd.DataFrame:
    """Reads the daily total sunspot numbers of a SILSO CSV file.

    Rows without a valid date are skipped; invalid values and the placeholders of days without a
    sunspot number become NA in their own field only.

    Args:
        path: The path of the semicolon-separated SILSO file.

    Returns:
        One row per valid day with a timezone-aware UTC DatetimeIndex and the columns sunspot_number,
        std_dev, num_observations, is_provisional and decimal_year.

    Raises:
        FileNotFoundError: If the file does not exist.
        DataProcessingError: If the file cannot be read or holds no row with a valid date.
    """
    raw_rows = _read_raw_rows(path, _DAILY_COLUMNS)
    numeric, timestamps = _rows_with_valid_date(raw_rows)
    is_missing_day = numeric["sunspot_number"] == _MISSING_VALUE
    is_placeholder = pd.DataFrame({field: is_missing_day for field in _PLACEHOLDER_FIELDS})
    measurements = {
        "sunspot_number": _to_count(numeric["sunspot_number"]),
        "std_dev": numeric["std_dev"],
        "num_observations": _to_count(numeric["num_observations"]),
    }
    return _build_frame(path, raw_rows.loc[numeric.index], numeric, timestamps, measurements, is_placeholder)


def parse_silso_monthly(path: Path) -> pd.DataFrame:
    """Reads the monthly mean or the 13-month smoothed total sunspot numbers of a SILSO CSV file.

    Each month is stamped at its first day 00:00 UTC. Rows without a valid month are skipped; invalid
    values and -1 placeholders become NA in their own field only, so a month without a value is kept.

    Args:
        path: The path of the semicolon-separated SILSO file.

    Returns:
        One row per valid month with a timezone-aware UTC DatetimeIndex and the columns sunspot_number,
        std_dev, num_observations, is_provisional and decimal_year.

    Raises:
        FileNotFoundError: If the file does not exist.
        DataProcessingError: If the file cannot be read or holds no row with a valid date.
    """
    raw_rows = _read_raw_rows(path, _MONTHLY_COLUMNS)
    numeric, timestamps = _rows_with_valid_date(raw_rows)
    is_placeholder = numeric[list(_PLACEHOLDER_FIELDS)] == _MISSING_VALUE
    measurements = {
        "sunspot_number": numeric["sunspot_number"],
        "std_dev": numeric["std_dev"],
        "num_observations": _to_count(numeric["num_observations"]),
    }
    return _build_frame(path, raw_rows.loc[numeric.index], numeric, timestamps, measurements, is_placeholder)


def _read_raw_rows(path: Path, column_names: tuple[str, ...]) -> pd.DataFrame:
    """Reads the semicolon-separated SILSO file with every field as text.

    Args:
        path: The path of the SILSO file.
        column_names: The column order of the file.

    Returns:
        The raw rows of the file.

    Raises:
        FileNotFoundError: If the file does not exist.
        DataProcessingError: If the file cannot be read.
    """
    if not path.exists():
        raise FileNotFoundError(f"SILSO data file not found at {path}")
    try:
        raw_rows = pd.read_csv(
            path,
            sep=";",
            names=list(column_names),
            # Text columns let each field be converted and validated on its own.
            dtype=str,
            skipinitialspace=True,
        )
    except (OSError, UnicodeDecodeError, pd.errors.ParserError) as error:
        raise DataProcessingError(f"SILSO data file {path} cannot be read: {error}") from error
    logger.info("Extracted %d rows from %s.", len(raw_rows), path)
    return raw_rows


def _rows_with_valid_date(raw_rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Converts the raw rows to numbers and keeps the rows with a valid date.

    Args:
        raw_rows: The raw rows of the file, all fields as text.

    Returns:
        The numeric rows with a valid date and their UTC time stamps.
    """
    # An unparsable value becomes NA in its own column only, so the rest of the row stays usable.
    numeric = raw_rows.apply(pd.to_numeric, errors="coerce")
    # Monthly rows have no day column and stand for the first day of their month.
    date_parts = numeric.reindex(columns=["year", "month", "day"], fill_value=1)
    timestamps_all = pd.to_datetime(date_parts, errors="coerce").dt.tz_localize("UTC")
    has_valid_date = timestamps_all.notna()
    if not has_valid_date.all():
        logger.warning("Skipped %d rows without a valid date.", (~has_valid_date).sum())
    return numeric[has_valid_date], pd.DatetimeIndex(timestamps_all[has_valid_date], name="timestamp")


def _build_frame(
    path: Path,
    raw_rows: pd.DataFrame,
    numeric: pd.DataFrame,
    timestamps: pd.DatetimeIndex,
    measurements: dict[str, pd.Series],
    is_placeholder: pd.DataFrame,
) -> pd.DataFrame:
    """Combines the converted fields of the rows with a valid date to the stored frame.

    Args:
        path: The path of the SILSO file, for the error message.
        raw_rows: The raw rows with a valid date, all fields as text.
        numeric: The same rows converted to numbers.
        timestamps: The UTC time stamps of the rows.
        measurements: The converted sunspot_number, std_dev and num_observations fields.
        is_placeholder: True where a measurement field holds a placeholder instead of a value.

    Returns:
        The stored fields indexed by their time stamp, placeholders and invalid values as NA.

    Raises:
        DataProcessingError: If no row has a valid date.
    """
    frame = pd.DataFrame(
        {
            **{field: values.mask(is_placeholder[field]) for field, values in measurements.items()},
            # SILSO marks provisional values with indicator 0 and definitive values with 1.
            "is_provisional": numeric["indicator"].map({0: True, 1: False}),
            "decimal_year": numeric["decimal_year"],
        }
    )
    if frame.empty:
        raise DataProcessingError(f"{path} holds no row with a valid time stamp.")
    _log_invalid_values(raw_rows, frame, is_placeholder)
    return frame.set_axis(timestamps)


def _log_invalid_values(raw_rows: pd.DataFrame, frame: pd.DataFrame, is_placeholder: pd.DataFrame) -> None:
    """Logs per field the number of values that are neither valid nor a documented placeholder.

    Args:
        raw_rows: The raw rows with a valid date, all fields as text.
        frame: The stored fields of the same rows, NA where a value is missing or invalid.
        is_placeholder: True where a measurement field holds a placeholder instead of a value.
    """
    # Renamed so the file column indicator lines up with the output field derived from it.
    raw_fields = raw_rows.rename(columns={"indicator": "is_provisional"})[frame.columns]
    invalid = frame.isna() & raw_fields.notna()
    invalid[is_placeholder.columns] &= ~is_placeholder
    for column, count in invalid.sum().items():
        if count:
            logger.warning("Omitted %d invalid values in field %s.", count, column)


def _to_count(values: pd.Series) -> pd.Series:
    """Converts whole numbers to a nullable integer column.

    Args:
        values: Numeric values, NA where the file held no parsable number.

    Returns:
        The values as Int64; fractional values become NA.
    """
    return values.where(values % 1 == 0).astype("Int64")
