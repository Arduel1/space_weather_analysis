import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, cast

import numpy as np
import pandas as pd
from netCDF4 import num2date

logger = logging.getLogger(__name__)


# --- Basic Time Utilities ---


def ensure_aware(dt: datetime) -> datetime:
    """
    Ensures a datetime object is timezone-aware (UTC).

    If the datetime object is naive (no timezone info), it is assumed to be UTC
    and assigned the UTC timezone. If it is already aware, it is returned as is.

    Args:
        dt (datetime): The datetime object to check.

    Returns:
        datetime: A timezone-aware datetime object (UTC).
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_iso_time(time_str: str) -> Optional[datetime]:
    """
    Safely parses an ISO time string into a timezone-aware datetime.

    Args:
        time_str (str): The ISO formatted time string (e.g., "2023-01-01T12:00:00Z").

    Returns:
        Optional[datetime]: A timezone-aware datetime object (UTC), or None if parsing fails.
    """
    try:
        dt = datetime.fromisoformat(time_str)
        return ensure_aware(dt)
    except Exception:
        logger.error(f"Failed to parse time string: {time_str}", exc_info=True)
        return None


def calculate_time_intersection(
    range1_start: datetime,
    range1_end: datetime,
    range2_start: datetime,
    range2_end: datetime,
) -> Optional[Tuple[datetime, datetime]]:
    """
    Calculates the overlapping period between two time ranges.

    All inputs are converted to timezone-aware (UTC) datetimes before calculation
    to ensure consistent comparison.

    Args:
        range1_start (datetime): Start of the first time range.
        range1_end (datetime): End of the first time range.
        range2_start (datetime): Start of the second time range.
        range2_end (datetime): End of the second time range.

    Returns:
        Optional[Tuple[datetime, datetime]]: A tuple containing the (start, end)
                                             of the overlapping period, or None
                                             if the ranges do not overlap (start >= end).
    """
    start = max(ensure_aware(range1_start), ensure_aware(range2_start))
    end = min(ensure_aware(range1_end), ensure_aware(range2_end))

    if start < end:
        return start, end
    return None


# --- Heuristic Time Discovery & Decoding ---


def attempt_numeric_time_conversions(
    raw_vals: np.ndarray,
    plausible_year_min: Optional[int] = None,
    plausible_year_max: Optional[int] = None,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Attempts standard numeric-to-datetime transformations (Unix, Julian Day, etc.).

    Can optionally evaluate plausibility by checking if the resulting years fall within
    a specified range. If no range is provided, any valid timestamp is accepted.

    Transformations include Unix timestamps (s, ms, us), Days since 1970/2000,
    Julian Days, Modified Julian Days, and MATLAB datenums.

    Args:
        raw_vals (np.ndarray): 1-D numpy array of numeric values.
        plausible_year_min (Optional[int]): Earliest plausible year (inclusive).
                                            If None, no lower bound check is performed.
        plausible_year_max (Optional[int]): Latest plausible year (inclusive).
                                            If None, no upper bound check is performed.

    Returns:
        Tuple[Optional[np.ndarray], Dict[str, Any]]: A tuple containing the best decoded
                                                     array (or None) and a diagnostics dictionary.
    """
    diagnostics: Dict[str, Any] = {"tried": []}
    arr = np.asarray(raw_vals)

    # Handle masked arrays by filling with NaN to avoid runtime errors in pandas
    if np.ma.is_masked(arr):
        arr = cast(np.ma.MaskedArray, arr).filled(np.nan)

    if np.all(pd.isna(arr)):
        diagnostics["error"] = "raw array all NaN"
        return None, diagnostics

    transforms: List[Tuple[str, pd.DatetimeIndex, Dict[str, Any]]] = []

    # Constants for epoch conversions (Days relative to Unix Epoch 1970-01-01)
    # Julian Day Epoch (4713 BC) -> Unix Epoch: 2,440,587.5 days
    JD_UNIX_OFFSET_DAYS = 2440587.5
    # Modified Julian Day Epoch (1858) -> Unix Epoch: 40,587 days
    MJD_UNIX_OFFSET_DAYS = 40587.0
    # Matlab Epoch (Year 0) -> Unix Epoch: 719,529 days
    MATLAB_UNIX_OFFSET_DAYS = 719529.0

    SECONDS_PER_DAY = 86400.0

    # Strategy definition: (Name, Pandas Unit, Pandas Origin, Pre-processing Func)
    strategies: List[Tuple[str, str, str, Optional[Callable[[np.ndarray], np.ndarray]]]] = [
        ("unix_seconds", "s", "unix", None),
        ("unix_millis", "ms", "unix", None),
        ("unix_micros", "us", "unix", None),
        ("days_since_1970", "D", "1970-01-01", None),
        ("days_since_2000", "D", "2000-01-01", None),
        # For Julian Days, we shift to Unix Epoch 0 and convert to seconds for robust handling
        ("julian_day", "s", "unix", lambda x: (x - JD_UNIX_OFFSET_DAYS) * SECONDS_PER_DAY),
        ("modified_julian_day", "s", "unix", lambda x: (x - MJD_UNIX_OFFSET_DAYS) * SECONDS_PER_DAY),
        ("matlab_datenum", "s", "unix", lambda x: (x - MATLAB_UNIX_OFFSET_DAYS) * SECONDS_PER_DAY),
    ]

    for name, unit, origin, pre_process in strategies:
        try:
            # Apply transformation math if defined
            input_data = pre_process(arr) if pre_process else arr

            # Wrap in pd.Index to satisfy strict type checkers demanding a Sequence/Index
            input_index = pd.Index(input_data)
            dt = pd.to_datetime(input_index, unit=unit, origin=origin, errors="coerce")

            if isinstance(dt, pd.DatetimeIndex):
                stats = _score_dtindex(dt, plausible_year_min, plausible_year_max)
                diagnostics["tried"].append({"transform": name, **stats})
                transforms.append((name, dt, stats))

        except Exception as e:
            diagnostics["tried"].append({"transform": name, "error": str(e)})

    return _select_best_transform(transforms, diagnostics)


# --- Internal Helper Functions ---


def _score_dtindex(dtindex: pd.DatetimeIndex, min_year: Optional[int], max_year: Optional[int]) -> Dict[str, Any]:
    """
    Calculates validity score of a DatetimeIndex based on range (if provided).

    Args:
        dtindex (pd.DatetimeIndex): The index to score.
        min_year (Optional[int]): Minimum plausible year.
        max_year (Optional[int]): Maximum plausible year.

    Returns:
        Dict[str, Any]: Statistics including fraction of valid/in-range dates.
    """
    total = len(dtindex)
    if total == 0:
        return {"non_na_frac": 0.0, "in_range_frac": 0.0, "valid_count": 0}

    non_na = (~dtindex.isna()).sum()
    if non_na == 0:
        return {"non_na_frac": 0.0, "in_range_frac": 0.0, "valid_count": 0}

    # Start with mask of valid dates (not NaT)
    mask = ~dtindex.isna()

    if min_year is None and max_year is None:
        in_range_count = mask.sum()
    else:
        # Apply year constraints only if specified
        if min_year is not None:
            # Sichere Variante: Prüfe erst auf NaT, bevor wir .year verwenden
            year_mask = dtindex.year >= min_year
            mask &= year_mask

        if max_year is not None:
            year_mask = dtindex.year <= max_year
            mask &= year_mask

        in_range_count = mask.sum()

    return {
        "non_na_frac": float(non_na) / total,
        "in_range_frac": float(in_range_count) / total,
        "valid_count": int(non_na),
    }


def _select_best_transform(
    transforms: List[Tuple[str, pd.DatetimeIndex, Dict[str, Any]]],
    diagnostics: Dict[str, Any],
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Selects the best time transform based on weighted scores with strict range enforcement.

    The scoring prioritizes transforms that produce dates within the specified plausible range.
    If a range is specified, transforms with no in-range dates are strictly rejected.

    Args:
        transforms: List of tuples containing (name, dtindex, stats).
        diagnostics: Diagnostic dictionary to update.

    Returns:
        Tuple: Best decoded numpy array (or None) and updated diagnostics.
    """
    best_score = -1.0
    best_choice = None

    for name, dt, stats in transforms:
        # Skip transforms with no valid dates
        if stats["non_na_frac"] <= 0:
            continue

        # STRICT RANGE ENFORCEMENT: If we have valid dates but NONE are in the specified range,
        # completely reject this transform to avoid false positives
        if stats["non_na_frac"] > 0 and stats["in_range_frac"] == 0.0:
            continue

        # Calculate weighted score (validity slightly more important than range)
        score_val = (stats["non_na_frac"] * 0.6) + (stats["in_range_frac"] * 0.4)

        if score_val > best_score:
            best_score = score_val
            best_choice = (name, dt, stats, score_val)

    diagnostics["best_score"] = best_score

    # Accept if we found a reasonably good transform
    if best_choice is not None and best_score > 0.01:
        name, dtindex, stats, score_val = best_choice
        diagnostics["accepted"] = True
        diagnostics["selected_transform"] = name
        diagnostics["selected_stats"] = stats
        diagnostics["score_value"] = score_val
        return dtindex.to_numpy(), diagnostics

    diagnostics["accepted"] = False
    diagnostics["reason"] = "no plausible transform found (low score or range mismatch)"
    return None, diagnostics


def ensure_datetime_type(val: Any) -> datetime:
    """
    Converts diverse datetime types (numpy/pandas) to a standardized Python datetime in UTC.

    Args:
        val (Any): Input value (pd.Timestamp, np.datetime64, or datetime).

    Returns:
        datetime: A timezone-aware (UTC) datetime object.
    """
    dt: datetime

    if isinstance(val, pd.Timestamp):
        dt = val.to_pydatetime()
    elif isinstance(val, np.datetime64):
        # Convert numpy -> pandas -> python to handle nanosecond precision edge cases gracefully
        dt = pd.Timestamp(val).to_pydatetime()
    else:
        # Assume it's already a datetime object
        dt = cast(datetime, val)

    # Enforce UTC awareness
    return ensure_aware(dt)


def convert_cftime_object(val: Any) -> Optional[datetime]:
    """
    Converts a cftime object to a standard Python datetime.

    Assumes the cftime object has year, month, day attributes.
    Handles potential calendar mismatches (e.g. 360_day calendar dates like Feb 30)
    by returning None instead of crashing.

    Args:
        val (Any): The cftime object.

    Returns:
        Optional[datetime]: A timezone-aware (UTC) datetime object, or None if conversion fails.
    """
    try:
        return datetime(
            int(val.year),
            int(val.month),
            int(val.day),
            int(getattr(val, "hour", 0)),
            int(getattr(val, "minute", 0)),
            int(getattr(val, "second", 0)),
            int(getattr(val, "microsecond", 0)),
            tzinfo=timezone.utc,
        )
    except ValueError as e:
        logger.debug(f"Failed to convert cftime object '{val}' to standard datetime: {e}")
        return None
    except AttributeError:
        # Handles cases where 'val' might not be a proper cftime object
        return None


def parse_string_timestamp(val: Union[str, bytes, np.str_, np.bytes_]) -> Optional[datetime]:
    """
    Parses a string or bytes representation of a timestamp using Pandas.

    Args:
        val (Union[str, bytes, np.str_, np.bytes_]): The input string/bytes.

    Returns:
        Optional[datetime]: The parsed datetime or None.
    """
    try:
        s = val.decode() if isinstance(val, (bytes, bytearray, np.bytes_)) else str(val)
        parsed = pd.to_datetime([s], errors="coerce")[0]
        if not pd.isna(parsed):
            return ensure_datetime_type(parsed)
    except Exception:
        pass
    return None


def parse_numeric_with_units(val: float, units: str, calendar: Any) -> Optional[datetime]:
    """
    Parses a numeric time value using netCDF4.num2date and unit string.

    Args:
        val (float): Numeric value.
        units (str): Units string (e.g. "seconds since 1970...").
        calendar (Any): Calendar string or None.

    Returns:
        Optional[datetime]: Decoded datetime.
    """
    try:
        cal_str = str(calendar) if calendar is not None else "standard"

        # Wrap val in a list to satisfy type checkers demanding a sequence
        decoded = num2date([val], units=units, calendar=cal_str)

        if isinstance(decoded, (list, tuple, np.ndarray)) and len(decoded) > 0:
            result = decoded[0]
            if hasattr(result, "year"):
                if isinstance(result, datetime):
                    return ensure_datetime_type(result)
                return convert_cftime_object(result)
    except Exception:
        pass
    return None


def parse_numeric_fallback(val: float) -> Optional[datetime]:
    """
    Parses a numeric value using fallback heuristics (Unix, JD, etc.).

    Args:
        val (float): The numeric value to test.

    Returns:
        Optional[datetime]: The best guess datetime or None.
    """
    try:
        arr = np.array([val])
        decoded_arr, _ = attempt_numeric_time_conversions(arr)
        if decoded_arr is not None and len(decoded_arr) > 0:
            first_val = decoded_arr[0]
            if isinstance(first_val, (pd.Timestamp, datetime, np.datetime64)):
                return ensure_datetime_type(first_val)
    except Exception:
        pass
    return None
