import logging
from datetime import datetime
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import xarray as xr

from space_weather_analysis.util.time_utils import (
    attempt_numeric_time_conversions,
    convert_cftime_object,
    ensure_datetime_type,
    parse_numeric_fallback,
    parse_numeric_with_units,
    parse_string_timestamp,
)

logger = logging.getLogger(__name__)


class TimeConfidence(IntEnum):
    """Defines confidence levels for identifying time variables."""

    NONE = 0
    LOW = 1  # Heuristic string matching (name contains 'time')
    HIGH = 2  # Explicit metadata (axis='T', standard_name='time')
    CERTAIN = 3  # Units match '... since ...' pattern (CF standard)


def calendar_is_present(attrs: Dict[str, Any]) -> bool:
    """
    Checks if a 'calendar' attribute is present and not None.

    Args:
        attrs (Dict[str, Any]): The attributes dictionary.

    Returns:
        bool: True if calendar is specified.
    """
    return attrs.get("calendar") is not None


def parse_xarray_timestamp(ts_slice: xr.Dataset, time_coord: str, index: int, allow_fallback: bool = False) -> Optional[datetime]:
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
            return ensure_datetime_type(raw_val)

        if hasattr(raw_val, "year") and hasattr(raw_val, "month"):
            return convert_cftime_object(raw_val)

        if isinstance(raw_val, (str, bytes, np.str_, np.bytes_)):
            return parse_string_timestamp(raw_val)

        if isinstance(raw_val, (float, int, np.number)) and isinstance(nc_units, str):
            # Convert numpy types to python float/int for cleaner type passing
            return parse_numeric_with_units(float(raw_val), nc_units, nc_calendar)

        if allow_fallback and isinstance(raw_val, (float, int, np.number)):
            return parse_numeric_fallback(float(raw_val))

        return None

    except Exception as e:
        logger.warning(f"parse_xarray_timestamp failed for '{time_coord}' at index {index}: {e}")
        return None


def find_time_dimension(ds: xr.Dataset) -> Tuple[Optional[str], Optional[str]]:
    """
    Identify a time coordinate and its dimension using file metadata logic.

    Args:
        ds (xr.Dataset): The dataset to inspect.

    Returns:
        Tuple[Optional[str], Optional[str]]: (time_dimension, time_variable_name)
    """
    # Check for existing datetime64 coordinates (decoded by xarray)
    logger.info("Scanning xarray coords for datetime64 types.")
    for coord_name, coord_val in ds.coords.items():
        if _is_datetime_type(coord_val):
            dims = ds.coords[coord_name].dims
            time_dim = dims[0] if dims else coord_name
            logger.info(f"Found time coordinate '{coord_name}' via dtype.")
            return str(time_dim), str(coord_name)

    # Analyze variable attributes
    logger.info("Scanning variables for time-candidate attributes.")
    best_var: Optional[str] = None
    max_confidence = TimeConfidence.NONE

    # Check xarray variables
    for name in ds.variables:
        confidence = _evaluate_variable_confidence(str(name), ds[name])

        # We prefer coordinates over data variables if confidence is equal
        is_coord = name in ds.coords

        if confidence > max_confidence:
            max_confidence = confidence
            best_var = str(name)
        elif confidence == max_confidence and confidence > TimeConfidence.NONE:
            # Prefer coordinates
            if is_coord and best_var not in ds.coords:
                best_var = str(name)

    # Fallback: Check underlying NetCDF object if accessible
    if max_confidence < TimeConfidence.CERTAIN:
        nc_best, nc_conf = _scan_netcdf_backend(ds)
        if nc_conf > max_confidence:
            best_var = nc_best

    if best_var:
        logger.info(f"Identified likely time variable: '{best_var}' (Confidence: {max_confidence})")
        return _resolve_dimension_for_variable(ds, best_var)

    logger.error("No candidate time variables found in file metadata.")
    return None, None


def find_heuristic_time_candidates(ds: xr.Dataset, time_dim: str) -> List[str]:
    """
    Identify numeric variables aligned with `time_dim` as plausible time candidates.

    This routine is heuristic and used by fallback logic. It prioritizes variables
    based on the standardized TimeConfidence score and their dimensionality (1D preferred).

    Args:
        ds (xr.Dataset): Dataset opened with decode_times=False.
        time_dim (str): The name of the dimension that corresponds to time.

    Returns:
        List[str]: A list of variable names ordered by plausibility (best first).
    """
    candidates: List[Tuple[str, int, bool]] = []

    for var_name in ds.variables:
        try:
            da = ds[var_name]

            # Must share the time dimension
            if time_dim not in da.dims:
                continue

            # Must be numeric
            if da.dtype.kind not in ("i", "f"):
                continue

            # Use existing confidence logic from this module
            # This checks for name patterns like 'time', 'epoch', etc.
            confidence = _evaluate_variable_confidence(str(var_name), da)

            # Prefer 1D variables (vectors) over multi-dimensional matrices
            is_1d = da.ndim == 1

            candidates.append((str(var_name), confidence, is_1d))

        except Exception as e:
            # Catch access errors for individual variables safely
            logger.debug(f"Skipping candidate check for {var_name}: {e}")
            continue

    candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)

    return [c[0] for c in candidates]


def _read_variable_sample(da: xr.DataArray, time_dim: str) -> np.ndarray:
    """
    Reads a sample of a variable, handling large sizes efficiently.

    Args:
        da (xr.DataArray): The DataArray to sample.
        time_dim (str): Dimension to slice along if subsampling is needed.

    Returns:
        np.ndarray: Flattened numpy array sample.
    """
    shape = da.shape
    total_size = int(np.prod(shape)) if shape else 0

    if total_size <= 50000:
        return da.values.reshape(-1)

    try:
        if time_dim in da.dims:
            # Create a slice dictionary: slice time, take 0 for others
            indexer = {d: slice(0, 1000) if d == time_dim else 0 for d in da.dims}
            return da.isel(indexer).values.reshape(-1)
    except Exception:
        pass

    # Fallback: Flat slicing
    return da.values.ravel()[:1000]


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
    identified by `find_heuristic_time_candidates`. It verifies the transform
    on a sample, but returns the fully converted array for the best candidate.

    Args:
        ds (xr.Dataset): Dataset opened with decode_times=False.
        time_dim (str): Name of the time dimension.
        allow_attempt (bool): If False, abort immediately.
        plausible_year_min (int): Year lower bound.
        plausible_year_max (int): Year upper bound.

    Returns:
        Tuple[Optional[np.ndarray], Dict[str, Any]]: Decoded times (full array) and diagnostics.
    """
    diagnostics: Dict[str, Any] = {"fallback_invoked": False, "candidates": []}

    if not allow_attempt:
        diagnostics["reason"] = "allow_attempt=False"
        return None, diagnostics

    diagnostics["fallback_invoked"] = True

    # Find candidates (uses the new logic in netcdf_utils)
    candidates = find_heuristic_time_candidates(ds, time_dim)
    diagnostics["candidates"] = candidates

    if not candidates:
        diagnostics["reason"] = "no_candidates"
        return None, diagnostics

    for cand in candidates:
        try:
            da = ds[cand]

            # Test on a small sample first for performance
            raw_sample = _read_variable_sample(da, time_dim)

            # Record inspection for debugging
            diagnostics.setdefault("inspect", []).append({"variable": cand, "shape": da.shape})

            # Try to decode the sample
            decoded_sample, diag = attempt_numeric_time_conversions(raw_sample, plausible_year_min, plausible_year_max)
            diagnostics.setdefault("attempts", {})[cand] = diag

            # If sample is valid, apply to FULL data
            if decoded_sample is not None and diag.get("accepted"):
                # Load full raw data (this might be large, but necessary if we want to use it)
                full_raw_data = da.values.ravel()

                # Apply the same conversion to the full dataset
                # We re-run attempt_numeric_time_conversions on the full set.
                # Ideally, we would force the specific successful strategy found in 'diag',
                # but for robustness/simplicity, we re-run the check. Since 'attempt'
                # is deterministic, it will pick the same winner.
                full_decoded, _ = attempt_numeric_time_conversions(full_raw_data, plausible_year_min, plausible_year_max)

                if full_decoded is not None:
                    diagnostics["selected_variable"] = cand
                    diagnostics["selected_transform"] = diag.get("selected_transform")
                    diagnostics["selected_stats"] = diag.get("selected_stats")
                    return full_decoded, diagnostics

        except Exception as e:
            diagnostics.setdefault("errors", []).append({cand: str(e)})
            continue

    diagnostics["reason"] = "no_plausible_decoding"
    return None, diagnostics


def _is_datetime_type(da: xr.DataArray) -> bool:
    """
    Checks if the data array has a datetime-like numpy dtype (datetime64).

    This check is performed lazily without loading the data into memory.

    Args:
        da (xr.DataArray): The DataArray to inspect.

    Returns:
        bool: True if the data type is datetime-like ('M'), False otherwise.
    """
    # Accessing .dtype on a DataArray reads metadata only and is safe/lazy.
    dtype = getattr(da, "dtype", None)
    return dtype is not None and dtype.kind == "M"


def _evaluate_variable_confidence(name: str, da: Any, attributes: Optional[Dict[str, Any]] = None) -> int:
    """
    Determines the likelihood of a variable being the time axis based on rules.

    Args:
        name (str): Variable name.
        da (Any): Xarray DataArray or NetCDF Variable.
        attributes (Optional[Dict]): Attributes override (useful for NetCDF backend vars).

    Returns:
        int: TimeConfidence integer level.
    """
    if attributes is None:
        attributes = getattr(da, "attrs", {}) or {}

    units = str(attributes.get("units", "")).lower()
    standard_name = str(attributes.get("standard_name", "")).lower()
    axis = str(attributes.get("axis", "")).upper()
    long_name = str(attributes.get("long_name", "")).lower()
    var_name = name.lower()

    # CF-Convention Standard (Certain)
    if "since" in units:
        return TimeConfidence.CERTAIN

    # Explicit Metadata (High Probability)
    if standard_name == "time" or axis == "T" or calendar_is_present(attributes):
        return TimeConfidence.HIGH

    # Name Heuristics (Low Probability)
    if var_name == "time" or var_name == "epoch":
        return TimeConfidence.LOW

    if "time" in long_name and "creation" not in long_name:
        return TimeConfidence.LOW

    return TimeConfidence.NONE


def _scan_netcdf_backend(ds: xr.Dataset) -> Tuple[Optional[str], int]:
    """
    Scans the hidden netCDF4 object for attributes lost by xarray.

    Accesses the private `_file_obj` attribute. This is risky but necessary
    for some backends where xarray drops coordinates or attributes.

    Returns:
        Tuple[Optional[str], int]: (best_variable_name, confidence_score)
    """
    try:
        # Access private attribute safely
        file_obj = getattr(ds, "_file_obj", None)
        nc_obj = getattr(file_obj, "ds", None)

        if nc_obj is None:
            return None, TimeConfidence.NONE.value

        best_var = None
        max_conf = TimeConfidence.NONE.value

        for name, var in nc_obj.variables.items():
            # Get attributes from NetCDF variable safely
            attrs = {}
            if hasattr(var, "ncattrs"):
                attrs = {k: getattr(var, k) for k in var.ncattrs()}

            conf = _evaluate_variable_confidence(str(name), var, attrs)

            if conf > max_conf:
                max_conf = conf
                best_var = str(name)

        return best_var, max_conf
    except Exception:
        return None, TimeConfidence.NONE.value


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


def _resolve_dimension_for_variable(ds: xr.Dataset, var_name: str) -> Tuple[str, str]:
    """
    Determines the dimension name associated with a variable name.

    Args:
        ds (xr.Dataset): Dataset.
        var_name (str): Variable name.

    Returns:
        Tuple[str, str]: (dimension_name, variable_name).
    """
    # Check in Xarray
    if var_name in ds.variables:
        dims = ds[var_name].dims
        time_dim = dims[0] if dims else var_name
        return str(time_dim), var_name

    # Check in NetCDF backend (if variable was dropped by xarray)
    nc_obj = _get_netcdf_object(ds)
    if nc_obj is not None and var_name in nc_obj.variables:
        dims = nc_obj.variables[var_name].dimensions
        time_dim = dims[0] if dims else var_name
        return str(time_dim), var_name

    # Fallback
    return var_name, var_name
