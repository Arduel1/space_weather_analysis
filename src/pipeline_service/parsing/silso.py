import logging
from pathlib import Path

import pandas as pd

from space_weather_analysis.util.error_handling import DataProcessingError

logger = logging.getLogger(__name__)

# Identifies the source and version of the SILSO (Sunspot Index and Long-term Solar Observations) data.
SILSO_PROVIDER_ID = "SILSO_V2.0"

# Column order of the daily total sunspot number file.
_COLUMN_NAMES = (
    "year",
    "month",
    "day",
    "decimal_year",
    "sunspot_number",
    "std_dev",
    "num_observations",
    "indicator",
)
# The sunspot number that SILSO writes on days without a number.
_MISSING_SUNSPOT_NUMBER = -1
# Fields that hold placeholders instead of measurements on days without a sunspot number.
_FIELDS_UNSET_ON_MISSING_DAY = ("sunspot_number", "std_dev", "num_observations")


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
    raw_rows = _read_raw_rows(path)
    frame = _build_frame(raw_rows)
    if frame.empty:
        raise DataProcessingError(f"{path} holds no row with a valid time stamp.")
    return frame


def _read_raw_rows(path: Path) -> pd.DataFrame:
    """Reads the semicolon-separated SILSO file with every field as text.

    Args:
        path: The path of the SILSO file.

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
            names=list(_COLUMN_NAMES),
            # Text columns let each field be converted and validated on its own.
            dtype=str,
            skipinitialspace=True,
        )
    except (OSError, UnicodeDecodeError, pd.errors.ParserError) as error:
        raise DataProcessingError(f"SILSO data file {path} cannot be read: {error}") from error
    logger.info("Extracted %d rows from %s.", len(raw_rows), path)
    return raw_rows


def _build_frame(raw_rows: pd.DataFrame) -> pd.DataFrame:
    """Converts the raw rows with a valid date to the stored fields, indexed by their date.

    Args:
        raw_rows: The raw rows of the file, all fields as text.

    Returns:
        The stored fields of the rows with a valid date.
    """
    # An unparsable value becomes NA in its own column only, so the rest of the row stays usable.
    numeric = raw_rows.apply(pd.to_numeric, errors="coerce")

    timestamps_all = pd.to_datetime(numeric[["year", "month", "day"]], errors="coerce").dt.tz_localize("UTC")
    has_valid_date = timestamps_all.notna()
    if not has_valid_date.all():
        logger.warning("Skipped %d rows without a valid date.", (~has_valid_date).sum())
    numeric = numeric[has_valid_date]

    is_missing = numeric["sunspot_number"] == _MISSING_SUNSPOT_NUMBER
    frame = pd.DataFrame(
        {
            "sunspot_number": _to_count(numeric["sunspot_number"]).mask(is_missing),
            "std_dev": numeric["std_dev"].mask(is_missing),
            "num_observations": _to_count(numeric["num_observations"]).mask(is_missing),
            # SILSO marks provisional values with indicator 0 and definitive values with 1.
            "is_provisional": numeric["indicator"].map({0: True, 1: False}),
            "decimal_year": numeric["decimal_year"],
        }
    )
    _log_invalid_values(raw_rows.loc[has_valid_date], frame, is_missing)
    return frame.set_axis(pd.DatetimeIndex(timestamps_all[has_valid_date], name="timestamp"))


def _log_invalid_values(raw_rows: pd.DataFrame, frame: pd.DataFrame, is_missing: pd.Series) -> None:
    """Logs per field the number of values that are neither valid nor a documented placeholder.

    Args:
        raw_rows: The raw rows with a valid date, all fields as text.
        frame: The stored fields of the same rows, NA where a value is missing or invalid.
        is_missing: True for the days without a sunspot number.
    """
    # Renamed so the file column indicator lines up with the output field derived from it.
    raw_fields = raw_rows.rename(columns={"indicator": "is_provisional"})[frame.columns]
    invalid = frame.isna() & raw_fields.notna()
    invalid.loc[is_missing, list(_FIELDS_UNSET_ON_MISSING_DAY)] = False
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
