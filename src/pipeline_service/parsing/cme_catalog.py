import logging
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import pandas as pd

from space_weather_analysis.util.error_handling import DataProcessingError

logger = logging.getLogger(__name__)

# Identifies the source and version of the SOHO/LASCO catalog of coronal mass ejections (CME).
CME_PROVIDER_ID = "SOHO/LASCO_CDAW_V2.0"

_NUMERIC_COLUMNS = (
    "central_pa",
    "width",
    "linear_speed",
    "speed_initial",
    "speed_final",
    "speed_at_20r",
    "accel",
    "mass",
    "kinetic_energy",
    "mpa",
)
# Column order of the catalog file; the numeric columns sit between time and remarks.
_COLUMN_NAMES = ("date", "time", *_NUMERIC_COLUMNS, "remarks")
# The catalog file starts with 4 header lines.
_HEADER_LINE_COUNT = 4
# The remarks are the rest of the line after this number of splits.
_REMARKS_SPLIT_COUNT = len(_COLUMN_NAMES) - 1
# Dash runs that the catalog writes in place of an unknown value.
_PLACEHOLDERS = frozenset({"----", "------", "-------"})
# Columns in which a trailing '*' marks an uncertain value, mapped to the flag field storing that marker.
_MARKER_FLAGS: Mapping[str, str] = MappingProxyType(
    {
        "accel": "accel_unreliable",
        "mass": "mass_uncertain",
        "kinetic_energy": "kinetic_energy_uncertain",
    }
)
# The stored fields in their order.
_DATA_COLUMNS = (*_NUMERIC_COLUMNS, "is_halo", *_MARKER_FLAGS.values(), "remarks")


def parse_cme_catalog(path: Path) -> pd.DataFrame:
    """Reads the events of the SOHO/LASCO CME catalog text file.

    Rows without a valid time are skipped; placeholders and invalid numbers become NA in their own
    field only. A halo CME gets is_halo True and no central position angle; a trailing '*' sets the
    uncertainty flag of its column to True, unmarked values leave the flag NA.

    Args:
        path: The path of the space-delimited catalog file (univ_all.txt).

    Returns:
        One row per valid event with a timezone-aware UTC DatetimeIndex and one column per stored field.

    Raises:
        FileNotFoundError: If the file does not exist.
        DataProcessingError: If the file cannot be read or holds no row with a valid time.
    """
    frame = _build_frame(_read_raw_rows(path))
    if frame.empty:
        raise DataProcessingError(f"{path} holds no row with a valid time stamp.")
    return frame


def _read_raw_rows(path: Path) -> pd.DataFrame:
    """Splits the rows of the space-delimited catalog file into its columns, all as text.

    Args:
        path: The path of the catalog file.

    Returns:
        The raw rows of the file.

    Raises:
        FileNotFoundError: If the file does not exist.
        DataProcessingError: If the file cannot be read.
    """
    if not path.exists():
        raise FileNotFoundError(f"CME catalog file not found at {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise DataProcessingError(f"CME catalog file {path} cannot be read: {error}") from error

    parsed_rows: list[dict[str, str]] = []
    for line in lines[_HEADER_LINE_COUNT:]:
        # The remarks keep their inner spacing.
        parts = line.rstrip().split(maxsplit=_REMARKS_SPLIT_COUNT)
        if not parts:
            continue
        # Rows without remarks have one part less and get no remarks entry.
        parsed_rows.append(dict(zip(_COLUMN_NAMES, parts, strict=False)))
    logger.info("Extracted %d rows from %s.", len(parsed_rows), path)
    return pd.DataFrame(parsed_rows, columns=list(_COLUMN_NAMES))


def _build_frame(raw_rows: pd.DataFrame) -> pd.DataFrame:
    """Converts the raw rows with a valid time to the stored fields, indexed by their time.

    Args:
        raw_rows: The raw rows of the file, left unchanged.

    Returns:
        The stored fields of the rows with a valid time.
    """
    events = raw_rows.copy()
    for column, flag in _MARKER_FLAGS.items():
        is_marked = events[column].str.endswith("*", na=False)
        # The flag is True for marked values only; unmarked events get no flag value.
        events[flag] = is_marked.map({True: True, False: None})
        events[column] = events[column].str.removesuffix("*")

    timestamps = pd.to_datetime(
        events["date"].astype(str) + " " + events["time"].astype(str),
        format="%Y/%m/%d %H:%M:%S",
        errors="coerce",
    ).dt.tz_localize("UTC")
    has_valid_timestamp = timestamps.notna()
    if not has_valid_timestamp.all():
        logger.warning("Skipped %d rows without a valid timestamp.", (~has_valid_timestamp).sum())
    events = events[has_valid_timestamp].copy()

    events["is_halo"] = events["central_pa"].str.contains("Halo", na=False)
    # A halo CME has no central position angle; is_halo carries that information.
    events["central_pa"] = events["central_pa"].mask(events["is_halo"])

    for column in _NUMERIC_COLUMNS:
        is_placeholder = events[column].isin(_PLACEHOLDERS)
        numeric = pd.to_numeric(events[column].mask(is_placeholder), errors="coerce")
        # Values that are neither numbers nor placeholders are invalid and omitted like missing ones.
        invalid_count = (numeric.isna() & events[column].notna() & ~is_placeholder).sum()
        if invalid_count:
            logger.warning("Omitted %d invalid values in field %s.", invalid_count, column)
        events[column] = numeric

    frame = events[list(_DATA_COLUMNS)]
    return frame.set_axis(pd.DatetimeIndex(timestamps[has_valid_timestamp], name="timestamp"))
