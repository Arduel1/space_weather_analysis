import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union, cast

import numpy as np
import pandas as pd
import xarray as xr

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
    return dt


def parse_iso_time(time_str: str) -> datetime:
    """
    Safely parses an ISO time string into a timezone-aware datetime.

    This function handles the 'Z' suffix often used in space weather data
    (HAPI, CDAWeb) to denote UTC, ensuring compatibility with Python's
    standard library fromisoformat method.

    Args:
        time_str (str): The ISO formatted time string (e.g., "2023-01-01T12:00:00Z").

    Returns:
        datetime: A timezone-aware datetime object. Returns datetime.min (UTC)
                  if parsing fails, logged as an error.
    """
    try:
        if time_str.endswith("Z"):
            time_str = time_str[:-1] + "+00:00"

        dt = datetime.fromisoformat(time_str)
        return ensure_aware(dt)
    except Exception:
        logger.error(f"Failed to parse time string: {time_str}", exc_info=True)
        return datetime.min.replace(tzinfo=timezone.utc)


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


def find_time_dimension(ds: xr.Dataset) -> Tuple[Optional[str], Optional[str]]:
    """
    Identify a time coordinate and its dimension using file metadata.

    This function inspects xarray variables/coords and, if available, the underlying netCDF4
    variables to find the most likely time variable based on attributes like units,
    calendar, and standard names.

    Args:
        ds (xr.Dataset): The dataset to inspect.

    Returns:
        Tuple[Optional[str], Optional[str]]: A tuple of (time_dimension_name, time_variable_name),
                                             or (None, None) if no candidate is found.
    """
    try:
        logger.info("Scanning xarray coords for datetime64 types.")
        for coord_name, coord_val in ds.coords.items():
            arr = coord_val.values
            dtype = getattr(arr, "dtype", None)
            if dtype is not None and getattr(dtype, "kind", None) == "M":
                dims = ds.coords[coord_name].dims
                # Prefer the first dimension if multiple exist
                time_dim = dims[0] if dims else coord_name
                logger.info(f"Found time coordinate '{coord_name}' as datetime64 in coords (dims={dims}).")
                return str(time_dim), str(coord_name)
    except Exception:
        logger.info("Exception while scanning coords for datetime64; falling back to variable inspection.")

    logger.info("Scanning variables for time-candidate attributes.")

    # Using explicit types for candidates list
    candidates: List[Tuple[str, int, Dict[str, Any]]] = []

    # Iterate over variables using string keys to ensure access to DataArray objects
    for var_name in ds.variables:
        da = ds[var_name]
        score, details = _score_variable_attributes(str(var_name), da, source="xarray")
        if score > 0:
            candidates.append((str(var_name), score, details))

    nc_obj = _get_netcdf_object(ds)
    if nc_obj is not None:
        _scan_netcdf_variables(nc_obj, candidates)

    if not candidates:
        logger.error("No candidate time variables found in file metadata.")
        return None, None

    # Sort candidates by score descending
    candidates.sort(key=lambda item: item[1], reverse=True)

    _log_top_candidates(candidates)

    best_var, _, _ = candidates[0]

    return _resolve_dimension_for_variable(ds, best_var, nc_obj)


def find_heuristic_time_candidates(ds: xr.Dataset, time_dim: str) -> List[str]:
    """
    Identify numeric variables aligned with `time_dim` as plausible time candidates.

    This routine is heuristic and used only by fallback logic. It prefers variables
    that share the time dimension, are numeric, and ideally are 1D.

    Args:
        ds (xr.Dataset): Dataset opened with decode_times=False.
        time_dim (str): The name of the dimension that corresponds to time.

    Returns:
        List[str]: A list of variable names ordered by plausibility (highest score first).
    """
    candidates: List[Tuple[str, int]] = []

    for var_name in ds.variables:
        try:
            da = ds[var_name]
            if time_dim not in da.dims:
                continue

            dims = da.dims
            dtype = getattr(getattr(da, "values", None), "dtype", None)
            score = 0

            # Numeric dtype is required for numeric-to-time fallbacks
            if dtype is not None and dtype.kind in ("f", "i"):
                score += 50
            else:
                continue

            # Prefer 1D variables or variables where time is the first dimension
            if len(dims) == 1:
                score += 30
            elif dims[0] == time_dim:
                score += 15

            # Prefer small variables (cheap to read)
            shape = getattr(da, "shape", None)
            if shape and np.prod(shape) <= 1024:
                score += 5

            candidates.append((str(var_name), score))
        except Exception:
            continue

    candidates.sort(key=lambda item: item[1], reverse=True)
    return [c[0] for c in candidates]


def attempt_numeric_time_conversions(
    raw_vals: np.ndarray,
    plausible_year_min: int = 1950,
    plausible_year_max: int = 2050,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Attempts standard numeric-to-datetime transformations (Unix, Julian Day, etc.).

    Evaluates plausibility by checking if the resulting years fall within the
    specified plausible range. Transformations include Unix timestamps (s, ms, us),
    Days since 1970/2000, Julian Days, Modified Julian Days, and MATLAB datenums.

    Args:
        raw_vals (np.ndarray): 1-D numpy array of numeric values.
        plausible_year_min (int): Earliest plausible year.
        plausible_year_max (int): Latest plausible year.

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

    # Strategies definition: (Name, Unit, Origin, Offset in seconds)
    strategies = [
        ("unix_seconds", "s", "unix", 0.0),
        ("unix_millis", "ms", "unix", 0.0),
        ("unix_micros", "us", "unix", 0.0),
        ("days_since_1970", "D", "1970-01-01T00:00:00Z", 0.0),
        ("days_since_2000", "D", "2000-01-01T00:00:00Z", 0.0),
        ("julian_day", "s", "unix", -2440587.5 * 86400.0),
        ("modified_julian_day", "s", "unix", (2400000.5 - 2440587.5) * 86400.0),
        ("matlab_datenum", "s", "unix", -719529.0 * 86400.0),
    ]

    for name, unit, origin, offset_seconds in strategies:
        try:
            if offset_seconds != 0.0:
                # Calculate adjusted input for offset-based formats
                if name == "julian_day":
                    input_data = (arr - 2440587.5) * 86400.0
                elif name == "modified_julian_day":
                    input_data = (arr + 2400000.5 - 2440587.5) * 86400.0
                elif name == "matlab_datenum":
                    input_data = (arr - 719529.0) * 86400.0
                else:
                    input_data = arr
            else:
                input_data = arr

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


def fallback_decode_time(
    ds: xr.Dataset,
    time_dim: str,
    allow_attempt: bool = True,
    plausible_year_min: int = 1950,
    plausible_year_max: int = 2050,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Controlled fallback to decode time values when no file metadata exists.

    This function attempts various numeric interpretations on candidate variables
    identified by `find_heuristic_time_candidates`.

    Args:
        ds (xr.Dataset): Dataset opened with decode_times=False.
        time_dim (str): Name of the time dimension.
        allow_attempt (bool): If False, abort immediately.
        plausible_year_min (int): Year lower bound.
        plausible_year_max (int): Year upper bound.

    Returns:
        Tuple[Optional[np.ndarray], Dict[str, Any]]: Decoded times (or None) and diagnostics.
    """
    diagnostics: Dict[str, Any] = {"fallback_invoked": False, "candidates": []}

    if not allow_attempt:
        diagnostics["reason"] = "allow_attempt=False"
        return None, diagnostics

    diagnostics["fallback_invoked"] = True
    candidates = find_heuristic_time_candidates(ds, time_dim)
    diagnostics["candidates"] = candidates

    if not candidates:
        diagnostics["reason"] = "no_candidates"
        return None, diagnostics

    for cand in candidates:
        try:
            da = ds[cand]  # Access as DataArray
            raw_1d = _read_variable_sample(da, time_dim)

            diagnostics.setdefault("inspect", []).append({"variable": cand, "shape": getattr(da, "shape", None)})

            decoded, diag = attempt_numeric_time_conversions(raw_1d, plausible_year_min, plausible_year_max)
            diagnostics.setdefault("attempts", {})[cand] = diag

            if decoded is not None and diag.get("accepted"):
                diagnostics["selected_variable"] = cand
                diagnostics["selected_transform"] = diag.get("selected_transform")
                diagnostics["selected_stats"] = diag.get("selected_stats")
                return decoded, diagnostics

        except Exception as e:
            diagnostics.setdefault("errors", []).append({cand: str(e)})
            continue

    diagnostics["reason"] = "no_plausible_decoding"
    return None, diagnostics


def decode_time_variable_from_file(ds: xr.Dataset, time_coord: str) -> Optional[np.ndarray]:
    """
    Decodes a time variable using only file metadata (e.g. NetCDF attributes).

    Prefers using `netCDF4.num2date` if 'units' and 'calendar' attributes are present
    on the underlying NetCDF variable.

    Args:
        ds (xr.Dataset): The dataset.
        time_coord (str): The name of the variable to decode.

    Returns:
        Optional[np.ndarray]: Array of datetime objects or None.
    """
    if time_coord in ds:
        vals = ds[time_coord].values
        if vals is not None:
            # Check if already decoded by xarray open_dataset
            if vals.dtype.kind == "M" or vals.dtype == object:
                return vals

    nc_obj = _get_netcdf_object(ds)
    if nc_obj is not None and time_coord in nc_obj.variables:
        tvar = nc_obj.variables[time_coord]
        try:
            raw = tvar[:]
            units = getattr(tvar, "units", None)
            calendar = getattr(tvar, "calendar", None)

            if isinstance(units, str):
                from netCDF4 import num2date

                # Ensure calendar is a string, default to standard if None
                cal_str = str(calendar) if calendar is not None else "standard"

                # num2date expects sequence if we want sequence back
                decoded = num2date(raw, units=units, calendar=cal_str)
                return np.array(decoded)

            # Fallback for ISO strings in NetCDF
            if raw.dtype.kind in ("U", "S", "O"):
                parsed = pd.to_datetime(raw, errors="coerce")
                if parsed.notna().any():
                    return parsed.to_numpy()

            return np.array(raw)
        except Exception as e:
            logger.info(f"Failed to decode time variable '{time_coord}' via netCDF4: {e}")

    # Final fallback: Pandas parsing on whatever xarray loaded
    if time_coord in ds:
        try:
            vals = ds[time_coord].values
            parsed = pd.to_datetime(vals, errors="coerce")
            if parsed.notna().any():
                return parsed.to_numpy()
            return vals
        except Exception:
            pass

    return None


def parse_xarray_timestamp(
    ts_slice: xr.Dataset, time_coord: str, index: int, allow_fallback: bool = False
) -> Optional[datetime]:
    """
    Parses a single timestamp from an xarray Dataset slice.

    Handles multiple types (numpy datetime64, pandas Timestamp, cftime, strings, numeric).

    Args:
        ts_slice (xr.Dataset): A dataset slice obtained via `ds.isel`.
        time_coord (str): Name of the time coordinate.
        index (int): Index along the time dimension (for fallback read via NetCDF).
        allow_fallback (bool): Whether to use numeric heuristics if metadata is missing.

    Returns:
        Optional[datetime]: Timezone-aware UTC datetime or None.
    """
    try:
        raw_val = None
        nc_units = None
        nc_calendar = None

        if time_coord in ts_slice:
            val = ts_slice[time_coord].values
            if np.isscalar(val):
                raw_val = val
            elif np.asarray(val).size == 1:
                raw_val = np.asarray(val).flatten()[0]

        # Fallback to NetCDF object if xarray slice is empty or weird
        if raw_val is None:
            nc_obj = _get_netcdf_object(ts_slice)
            if nc_obj and time_coord in nc_obj.variables:
                nc_var = nc_obj.variables[time_coord]
                # Read single value safely using index
                raw_val = nc_var[index] if getattr(nc_var, "ndim", 1) > 0 else nc_var[0]
                nc_units = getattr(nc_var, "units", None)
                nc_calendar = getattr(nc_var, "calendar", None)

        if raw_val is None:
            return None

        if isinstance(raw_val, (pd.Timestamp, datetime, np.datetime64)):
            return _ensure_datetime_type(raw_val)

        if hasattr(raw_val, "year") and hasattr(raw_val, "month"):
            return _convert_cftime_object(raw_val)

        if isinstance(raw_val, (str, bytes, np.str_, np.bytes_)):
            return _parse_string_timestamp(raw_val)

        if isinstance(raw_val, (float, int, np.number)) and isinstance(nc_units, str):
            # Convert numpy types to python float/int for cleaner type passing
            return _parse_numeric_with_units(float(raw_val), nc_units, nc_calendar)

        if allow_fallback and isinstance(raw_val, (float, int, np.number)):
            return _parse_numeric_fallback(float(raw_val))

        return None

    except Exception as e:
        logger.warning(f"parse_xarray_timestamp failed for '{time_coord}' at index {index}: {e}")
        return None


# --- Internal Helper Functions ---


def _score_dtindex(dtindex: pd.DatetimeIndex, min_year: int, max_year: int) -> Dict[str, Any]:
    """
    Calculates validity score of a DatetimeIndex based on range.

    Args:
        dtindex (pd.DatetimeIndex): The index to score.
        min_year (int): Minimum plausible year.
        max_year (int): Maximum plausible year.

    Returns:
        Dict[str, Any]: Statistics including fraction of valid/in-range dates.
    """
    total = len(dtindex)
    non_na = (~dtindex.isna()).sum()

    if non_na == 0:
        return {"non_na_frac": 0.0, "in_range_frac": 0.0, "valid_count": 0}

    years = dtindex.year
    in_range_mask = (~dtindex.isna()) & (years >= min_year) & (years <= max_year)
    in_range_frac = in_range_mask.sum() / total

    return {
        "non_na_frac": non_na / total,
        "in_range_frac": in_range_frac,
        "valid_count": int(non_na),
    }


def _select_best_transform(
    transforms: List[Tuple[str, pd.DatetimeIndex, Dict[str, Any]]],
    diagnostics: Dict[str, Any],
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Selects the best time transform based on weighted scores.

    Args:
        transforms (List): List of tuples containing (name, dtindex, stats).
        diagnostics (Dict): Diagnostic dictionary to update.

    Returns:
        Tuple: Best decoded numpy array (or None) and updated diagnostics.
    """
    best_score = -1.0
    best_choice = None

    for name, dt, s in transforms:
        # Weight non-NaN percentage higher than range percentage
        score_val = (s["non_na_frac"] * 0.6) + (s["in_range_frac"] * 0.4)
        if score_val > best_score:
            best_score = score_val
            best_choice = (name, dt, s, score_val)

    diagnostics["best_score"] = best_score
    if best_choice is None or best_score <= 0.01:
        diagnostics["accepted"] = False
        diagnostics["reason"] = "no plausible transform found (low score)"
        return None, diagnostics

    name, dtindex, s, score_val = best_choice
    diagnostics["accepted"] = True
    diagnostics["selected_transform"] = name
    diagnostics["selected_stats"] = s
    diagnostics["score_value"] = score_val

    return dtindex.to_numpy(), diagnostics


def _calculate_attribute_score(
    units: Any, calendar: Any, axis: Any, standard_name: Any, long_name: Any
) -> Tuple[int, List[str]]:
    """
    Deterministically calculates a score for a variable based on its attributes.

    Args:
        units (Any): The units attribute.
        calendar (Any): The calendar attribute.
        axis (Any): The axis attribute.
        standard_name (Any): The standard_name attribute.
        long_name (Any): The long_name attribute.

    Returns:
        Tuple[int, List[str]]: Calculated score and list of matched reasons.
    """
    score = 0
    # Explicitly typed as List[str] to support append/extend
    reasons: List[str] = []

    if isinstance(units, str) and "since" in units.lower():
        score += 100
        reasons.append(f"units='{units}'")

    if calendar is not None:
        score += 50
        reasons.append(f"calendar='{calendar}'")

    if isinstance(axis, str) and axis.upper() == "T":
        score += 40
        reasons.append("axis='T'")

    if isinstance(standard_name, str) and "time" in standard_name.lower():
        score += 30
        reasons.append(f"standard_name='{standard_name}'")

    if isinstance(long_name, str) and "time" in long_name.lower():
        score += 20
        reasons.append(f"long_name='{long_name}'")

    return score, reasons


def _score_variable_attributes(name: str, da: xr.DataArray, source: str) -> Tuple[int, Dict[str, Any]]:
    """
    Scores a variable based on its attributes to determine if it is a time variable.

    Args:
        name (str): Variable name.
        da (xr.DataArray): DataArray object to inspect.
        source (str): Source identifier (e.g., 'xarray').

    Returns:
        Tuple[int, Dict]: Score and details dictionary.
    """
    # Create the list first to ensure it's a mutable List[str]
    reasons: List[str] = []

    dtype = getattr(getattr(da, "values", None), "dtype", None)
    if dtype is not None and getattr(dtype, "kind", None) == "M":
        score = 90
        reasons.append("xarray-decoded-datetime")
    else:
        score = 0

    attrs = getattr(da, "attrs", {}) or {}
    units = attrs.get("units", "")
    calendar = attrs.get("calendar", None)
    axis = attrs.get("axis", "")
    standard_name = attrs.get("standard_name", "")
    long_name = attrs.get("long_name", "")

    attr_score, attr_reasons = _calculate_attribute_score(units, calendar, axis, standard_name, long_name)
    score += attr_score
    reasons.extend(attr_reasons)

    if name in getattr(da, "coords", []):
        score += 10
        reasons.append("is_coordinate")

    # Construct the details dictionary after list operations are done
    details: Dict[str, Any] = {"source": source, "reasons": reasons}

    if score > 0:
        details.update({"units": units, "calendar": calendar, "dtype": str(dtype)})

    return score, details


def _scan_netcdf_variables(nc_obj: Any, candidates: List[Tuple[str, int, Dict[str, Any]]]) -> None:
    """
    Helper to scan underlying NetCDF variables directly.

    Useful when xarray drops certain attributes or variables.

    Args:
        nc_obj (Any): The underlying NetCDF4 dataset object.
        candidates (List): List to append found candidates to.
    """
    try:
        for var_name, ncvar in nc_obj.variables.items():
            if any(var_name == c[0] for c in candidates):
                continue

            units = getattr(ncvar, "units", "")
            calendar = getattr(ncvar, "calendar", None)
            axis = getattr(ncvar, "axis", "")
            standard_name = getattr(ncvar, "standard_name", "")
            long_name = getattr(ncvar, "long_name", "")

            score, reasons = _calculate_attribute_score(units, calendar, axis, standard_name, long_name)

            if score > 0:
                reasons.append("found_via_netcdf_attrs")
                details: Dict[str, Any] = {"source": "netcdf4", "reasons": reasons}
                candidates.append((str(var_name), score, details))
    except Exception:
        pass


def _log_top_candidates(candidates: List[Tuple[str, int, Dict[str, Any]]]) -> None:
    """
    Logs the top scored time variable candidates for debugging.

    Args:
        candidates (List): List of candidate tuples (name, score, details).
    """
    logger.info("Time variable candidates ranked (top 10):")
    for name, score, details in candidates[:10]:
        logger.info(f"  {name}: score={score}, details={details}")


def _resolve_dimension_for_variable(ds: xr.Dataset, var_name: str, nc_obj: Any) -> Tuple[str, str]:
    """
    Determines the dimension name associated with a variable name.

    Args:
        ds (xr.Dataset): Dataset.
        var_name (str): Variable name.
        nc_obj (Any): Underlying NetCDF object.

    Returns:
        Tuple[str, str]: (dimension_name, variable_name).
    """
    if var_name in ds.variables:
        dims = ds[var_name].dims
        time_dim = dims[0] if dims else var_name
        return str(time_dim), var_name

    if nc_obj is not None and var_name in nc_obj.variables:
        dims = nc_obj.variables[var_name].dimensions
        time_dim = dims[0] if dims else var_name
        return str(time_dim), var_name

    return var_name, var_name


def _get_netcdf_object(ds: Any) -> Any:
    """
    Safely retrieve the underlying netCDF4 object from an xarray Dataset.

    Args:
        ds (Any): The xarray Dataset or DataArray.

    Returns:
        Any: The underlying netCDF4.Dataset object if present, else None.
    """
    file_obj = getattr(ds, "_file_obj", None)
    return getattr(file_obj, "ds", None)


def _read_variable_sample(da: xr.DataArray, time_dim: str) -> np.ndarray:
    """
    Reads a sample of a variable, handling large sizes efficiently.

    If the variable is small, it reads the whole array. If large, it slices
    along the time dimension or takes a flat sample.

    Args:
        da (xr.DataArray): The DataArray to sample.
        time_dim (str): Dimension to slice along if subsampling is needed.

    Returns:
        np.ndarray: Flattened numpy array sample.
    """
    shape = getattr(da, "shape", None)
    total_size = int(np.prod(shape)) if shape is not None else 0

    if total_size <= 50000:
        return da.values.reshape(-1)

    try:
        # Explicitly pass dict to indexers parameter to satisfy Type Checkers
        # and ensure correct isel behavior.
        sel_dict = {str(d): 0 for d in da.dims if str(d) != time_dim}
        raw = da.isel(indexers=sel_dict).values
    except Exception:
        if time_dim in da.dims:
            # Fallback to explicit slice on time dim
            raw = da.isel(indexers={time_dim: slice(0, 8)}).values
        else:
            raw = da.values.flat[:8]

    return np.asarray(raw).reshape(-1)


def _ensure_datetime_type(val: Any) -> datetime:
    """
    Converts diverse datetime types (numpy/pandas) to a standardized Python datetime in UTC.

    Args:
        val (Any): Input value (pd.Timestamp, np.datetime64, or datetime).

    Returns:
        datetime: A timezone-aware (UTC) datetime object.
    """
    if isinstance(val, pd.Timestamp):
        dt = val.to_pydatetime()
    elif isinstance(val, np.datetime64):
        dt = pd.Timestamp(val).to_pydatetime()
    else:
        dt = val

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _convert_cftime_object(val: Any) -> datetime:
    """
    Converts a cftime object to a standard Python datetime.

    Assumes the cftime object has year, month, day attributes.

    Args:
        val (Any): The cftime object.

    Returns:
        datetime: A timezone-aware (UTC) datetime object.
    """
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


def _parse_string_timestamp(val: Union[str, bytes, np.str_, np.bytes_]) -> Optional[datetime]:
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
            return _ensure_datetime_type(parsed)
    except Exception:
        pass
    return None


def _parse_numeric_with_units(val: float, units: str, calendar: Any) -> Optional[datetime]:
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
        from netCDF4 import num2date

        cal_str = str(calendar) if calendar is not None else "standard"

        # Wrap val in a list to satisfy type checkers demanding a sequence
        decoded = num2date([val], units=units, calendar=cal_str)

        if isinstance(decoded, (list, tuple, np.ndarray)) and len(decoded) > 0:
            result = decoded[0]
            if hasattr(result, "year"):
                if isinstance(result, datetime):
                    return _ensure_datetime_type(result)
                return _convert_cftime_object(result)
    except Exception:
        pass
    return None


def _parse_numeric_fallback(val: float) -> Optional[datetime]:
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
                return _ensure_datetime_type(first_val)
    except Exception:
        pass
    return None
