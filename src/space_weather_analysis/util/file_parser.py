import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, cast

import numpy as np
import pandas as pd
import xarray as xr
from astropy.table import Table
from astropy.table import vstack as vstack_tables
from cdflib import CDF
from sunpy.timeseries import GenericTimeSeries, TimeSeries
from sunpy.util import expand_list
from sunpy.util.datatype_factory_base import NoMatchError

logger = logging.getLogger(__name__)


def _fallback_time_candidates(ds: xr.Dataset, time_dim: str) -> List[str]:
    """
    Identify numeric or short 1-D variables aligned with `time_dim` that are plausible
    candidates for containing timestamp-like values. This routine is HEURISTIC and is
    only used by fallback logic; it does NOT assume CF conventions, it only looks at
    what the file actually contains (dims, shapes, dtypes).

    Args:
        ds: xarray.Dataset opened with decode_times=False.
        time_dim: the name of the dimension that corresponds to time (from _find_time_dimension).

    Returns:
        A list of variable names (strings) that are plausible numeric time candidates,
        ordered with best heuristics first.
    """
    candidates: List[Tuple[str, int]] = []
    for var_name, da in ds.variables.items():
        try:
            # skip variables that do not include the time dimension
            if time_dim not in da.dims:
                continue
            # prefer 1D along time or variables where time is the first dimension
            dims = da.dims
            # read dtype without loading full array
            dtype = getattr(getattr(da, "values", None), "dtype", None)
            score = 0
            reasons: List[str] = []

            # numeric dtype is required for numeric-to-time fallbacks
            if dtype is not None and dtype.kind in ("f", "i"):
                score += 50
                reasons.append("numeric")
            else:
                # strings could be ISO timestamps but fallback numeric logic ignores them
                continue

            # if the variable has exactly time dimension or time is first dim, prefer it
            if len(dims) == 1:
                score += 30
                reasons.append("1D_time")
            elif dims[0] == time_dim:
                score += 15
                reasons.append("time_first_dim")

            # small variables (cheap to read) are preferred for inspection
            try:
                shape = getattr(da, "shape", None)
                if shape and np.prod(shape) <= 1024:
                    score += 5
                    reasons.append("small_shape")
            except Exception:
                pass

            candidates.append((var_name, score))
        except Exception:
            continue

    # sort by score descending and return names
    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in candidates]


def _attempt_numeric_time_conversions(
    raw_vals: np.ndarray,
    plausible_year_min: int = 1950,
    plausible_year_max: int = 2050,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Attempt a set of standard numeric -> datetime interpretations and return the
    best plausible decoding plus diagnostics.

    The attempted interpretations (common transforms) are:
      - unix_seconds (seconds since 1970-01-01)
      - unix_millis  (ms since 1970-01-01)
      - unix_micros  (us since 1970-01-01)
      - days_since_1970 (days since 1970-01-01)
      - days_since_2000 (days since 2000-01-01)
      - julian_day (Julian Day -> UTC)
      - modified_julian_day (MJD -> UTC)
      - matlab_datenum (MATLAB datenum -> UTC; using known offset to 1970 epoch)

    NOTES:
      - This function uses ONLY the numeric values provided (no file-level attributes).
      - It evaluates plausibility by checking the percentage of non-NaT values and the
        share of dates that fall in the [plausible_year_min, plausible_year_max] range.
      - Returns the best decoded array (numpy datetime64[ns] or object) and diagnostics.

    Args:
        raw_vals: 1-D numpy array of numeric values (floats/ints).
        plausible_year_min: earliest plausible year (inclusive).
        plausible_year_max: latest plausible year (inclusive).

    Returns:
        (best_decoded_array_or_None, diagnostics_dict)
    """
    diagnostics: Dict[str, Any] = {"tried": []}
    # ensure numpy array
    arr = np.asarray(raw_vals)
    # convert masked arrays to normal with np.nan
    if np.ma.isMaskedArray(arr):
        arr = arr.filled(np.nan)

    # quick guard: if all NaN, bail out
    if np.all(pd.isna(arr)):
        diagnostics["error"] = "raw array all NaN"
        return None, diagnostics

    transforms = []

    # We'll use pandas.to_datetime for robust vectorized handling
    origin_unix = "unix"
    ts_1970 = pd.Timestamp("1970-01-01T00:00:00Z")
    ts_2000 = pd.Timestamp("2000-01-01T00:00:00Z")

    # Helper for scoring a parsed DatetimeIndex
    def score_dtindex(dtindex: pd.DatetimeIndex) -> Dict[str, Any]:
        total = len(dtindex)
        non_na = (~dtindex.isna()).sum()
        if non_na == 0:
            return {"non_na_frac": 0.0, "in_range_frac": 0.0, "valid_count": 0}
        # years in range (for non-na only)
        years = dtindex.year
        in_range_mask = (~dtindex.isna()) & (years >= plausible_year_min) & (years <= plausible_year_max)
        in_range_frac = in_range_mask.sum() / total
        return {"non_na_frac": non_na / total, "in_range_frac": in_range_frac, "valid_count": int(non_na)}

    try:
        # unix seconds
        try:
            dt = pd.to_datetime(arr, unit="s", origin=origin_unix, errors="coerce")
            s = score_dtindex(dt)
            diagnostics["tried"].append({"transform": "unix_seconds", **s})
            transforms.append(("unix_seconds", dt, s))
        except Exception as e:
            diagnostics["tried"].append({"transform": "unix_seconds", "error": str(e)})

        # unix millis
        try:
            dt = pd.to_datetime(arr, unit="ms", origin=origin_unix, errors="coerce")
            s = score_dtindex(dt)
            diagnostics["tried"].append({"transform": "unix_millis", **s})
            transforms.append(("unix_millis", dt, s))
        except Exception as e:
            diagnostics["tried"].append({"transform": "unix_millis", "error": str(e)})

        # unix micros
        try:
            dt = pd.to_datetime(arr, unit="us", origin=origin_unix, errors="coerce")
            s = score_dtindex(dt)
            diagnostics["tried"].append({"transform": "unix_micros", **s})
            transforms.append(("unix_micros", dt, s))
        except Exception as e:
            diagnostics["tried"].append({"transform": "unix_micros", "error": str(e)})

        # days since 1970
        try:
            dt = pd.to_datetime(arr, unit="D", origin=ts_1970, errors="coerce")
            s = score_dtindex(dt)
            diagnostics["tried"].append({"transform": "days_since_1970", **s})
            transforms.append(("days_since_1970", dt, s))
        except Exception as e:
            diagnostics["tried"].append({"transform": "days_since_1970", "error": str(e)})

        # days since 2000
        try:
            dt = pd.to_datetime(arr, unit="D", origin=ts_2000, errors="coerce")
            s = score_dtindex(dt)
            diagnostics["tried"].append({"transform": "days_since_2000", **s})
            transforms.append(("days_since_2000", dt, s))
        except Exception as e:
            diagnostics["tried"].append({"transform": "days_since_2000", "error": str(e)})

        # Julian Day (JD) -> compute seconds since unix epoch: (JD - 2440587.5) * 86400
        try:
            seconds = (arr - 2440587.5) * 86400.0
            dt = pd.to_datetime(seconds, unit="s", origin=origin_unix, errors="coerce")
            s = score_dtindex(dt)
            diagnostics["tried"].append({"transform": "julian_day", **s})
            transforms.append(("julian_day", dt, s))
        except Exception as e:
            diagnostics["tried"].append({"transform": "julian_day", "error": str(e)})

        # Modified Julian Day (MJD) -> JD = MJD + 2400000.5
        try:
            seconds = (arr + 2400000.5 - 2440587.5) * 86400.0  # (MJD -> JD -> seconds since unix)
            dt = pd.to_datetime(seconds, unit="s", origin=origin_unix, errors="coerce")
            s = score_dtindex(dt)
            diagnostics["tried"].append({"transform": "modified_julian_day", **s})
            transforms.append(("modified_julian_day", dt, s))
        except Exception as e:
            diagnostics["tried"].append({"transform": "modified_julian_day", "error": str(e)})

        # MATLAB datenum: MATLAB datenum for 1970-01-01 is 719529
        try:
            seconds = (arr - 719529) * 86400.0
            dt = pd.to_datetime(seconds, unit="s", origin=origin_unix, errors="coerce")
            s = score_dtindex(dt)
            diagnostics["tried"].append({"transform": "matlab_datenum", **s})
            transforms.append(("matlab_datenum", dt, s))
        except Exception as e:
            diagnostics["tried"].append({"transform": "matlab_datenum", "error": str(e)})
    except Exception as e_outer:
        diagnostics["error"] = f"exception during transforms: {e_outer}"
        return None, diagnostics

    # choose best transform by weighted score: prefer high non_na_frac and high in_range_frac
    best_score = -1.0
    best_choice = None
    for name, dt, s in transforms:
        # scoring function: product of non_na_frac * in_range_frac; weight non_na higher
        score_val = (s["non_na_frac"] * 0.6) + (s["in_range_frac"] * 0.4)
        # also prefer transforms that produced more valid_count if tie
        if score_val > best_score:
            best_score = score_val
            best_choice = (name, dt, s, score_val)

    diagnostics["best_score"] = best_score
    if best_choice is None or best_score <= 0.01:
        diagnostics["accepted"] = False
        diagnostics["reason"] = "no plausible transform found (low score)"
        return None, diagnostics

    # finalize
    name, dtindex, s, score_val = best_choice
    diagnostics["accepted"] = True
    diagnostics["selected_transform"] = name
    diagnostics["selected_stats"] = s
    diagnostics["score_value"] = score_val
    # return numpy datetime64[ns] array for compatibility
    decoded_arr = dtindex.to_numpy()
    return decoded_arr, diagnostics


def _fallback_decode_time(
    ds: xr.Dataset,
    time_dim: str,
    allow_attempt: bool = True,
    plausible_year_min: int = 1950,
    plausible_year_max: int = 2050,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """
    Controlled fallback that attempts to decode time values when no file-provided
    time metadata (units/calendar/decoded values) exists.

    This function:
      - selects candidate variables aligned with `time_dim` (_fallback_time_candidates),
      - attempts a set of numeric->datetime transforms (_attempt_numeric_time_conversions),
      - validates results via plausibility checks (fraction non-NaT, year range),
      - returns the best decoding (numpy datetime64 or None) plus a diagnostics dict.

    IMPORTANT:
      - This function uses heuristics and MUST only be invoked as a last resort.
      - Caller must check diagnostics and must store diagnostics in metadata so that
        downstream users can review what transform (if any) was applied.

    Args:
        ds: xarray.Dataset opened with decode_times=False.
        time_dim: the name of the dataset's time dimension.
        allow_attempt: if False, the function returns immediately without attempting.
        plausible_year_min: earliest plausible year to accept.
        plausible_year_max: latest plausible year to accept.

    Returns:
        (decoded_array_or_None, diagnostics_dict)
    """
    diagnostics: Dict[str, Any] = {"fallback_invoked": False, "candidates": []}
    if not allow_attempt:
        diagnostics["reason"] = "allow_attempt=False"
        return None, diagnostics

    diagnostics["fallback_invoked"] = True

    # find candidate variables that have this time dimension
    candidates = _fallback_time_candidates(ds, time_dim)
    diagnostics["candidates"] = candidates

    if not candidates:
        diagnostics["reason"] = "no_candidates"
        return None, diagnostics

    # iterate candidates in order of heuristic preference
    for cand in candidates:
        try:
            da = ds.variables[cand]
            # read raw values but avoid loading huge arrays: if huge, read a sample
            shape = getattr(da, "shape", None)
            total_size = int(np.prod(shape)) if shape is not None else None
            if total_size is None or total_size <= 50000:
                # safe to read whole variable
                raw = da.values
            else:
                # very large -> read first slice along time_dim only
                # find index of time_dim in dims
                try:
                    dim_index = da.dims.index(time_dim)
                    # take first element along other dims to make it cheap (result still 1D along time)
                    sel = {d: 0 for d in da.dims if d != time_dim}
                    raw = da.isel(**sel).values
                except Exception:
                    # fallback: read very small sample
                    raw = da.isel({time_dim: slice(0, min(8, shape[dim_index]))}).values
            # flatten to 1D
            raw_1d = np.asarray(raw).reshape(-1)
            diagnostics.setdefault("inspect", []).append({"variable": cand, "shape": getattr(da, "shape", None)})

            decoded, diag = _attempt_numeric_time_conversions(raw_1d, plausible_year_min, plausible_year_max)
            diagnostics.setdefault("attempts", {})[cand] = diag

            if decoded is not None and diag.get("accepted"):
                # attach which variable and transform selected
                diagnostics["selected_variable"] = cand
                diagnostics["selected_transform"] = diag.get("selected_transform")
                diagnostics["selected_stats"] = diag.get("selected_stats")
                # We must align decoded back to the dimension length of the dataset's time_dim.
                # If decoded length != ds.sizes[time_dim] then we keep decoded as-is (caller must inspect)
                return decoded, diagnostics

        except Exception as e:
            diagnostics.setdefault("errors", []).append({cand: str(e)})
            continue

    diagnostics["reason"] = "no_plausible_decoding"
    return None, diagnostics


def _log_dataset_summary(ds: xr.Dataset, file_path_obj: Optional[Path] = None) -> None:
    """
    Log a compact, safe summary of an xarray Dataset without loading large data arrays.

    The summary includes:
      - top-level dataset dims (names and sizes)
      - coordinates (name, dims, dtype.kind, length)
      - variables (name, dims, shape, dtype.kind, whether it's a coord)
      - selected attributes per variable (units, calendar, axis, standard_name, long_name)
      - if available, information from the underlying netCDF4.Dataset (variable list,
        dims and the same key attributes). This uses only attribute metadata and shapes.

    Args:
        ds: An xarray.Dataset (opened but not fully loaded).
        file_path_obj: Optional Path for contextual logging (filename).
    """
    prefix = f"{file_path_obj.name} - " if file_path_obj is not None else ""
    try:
        logger.info(f"{prefix}Dataset summary: dims={dict(ds.dims)}")

        # coords summary
        coord_names = list(ds.coords)
        logger.info(f"{prefix}Coordinates ({len(coord_names)}): {coord_names}")
        for coord in coord_names:
            try:
                da = ds.coords[coord]
                dtype = getattr(getattr(da, "values", None), "dtype", None)
                kind = getattr(dtype, "kind", None) if dtype is not None else None
                length = int(np.prod(da.sizes.values())) if getattr(da, "sizes", None) else None
                logger.info(f"{prefix}  COORD '{coord}': dims={da.dims}, dtype={dtype}, kind={kind}, length={length}")
                # log small sample (only if length small)
                if length is not None and length <= 8:
                    try:
                        sample = da.values[: min(8, length)]
                        logger.info(f"{prefix}    sample: {sample}")
                    except Exception:
                        pass
            except Exception as e:
                logger.info(f"{prefix}  Could not inspect coord '{coord}': {e}")

        # variables summary
        var_names = list(ds.variables)
        logger.info(f"{prefix}All variables ({len(var_names)}): {var_names}")
        for var in var_names:
            try:
                da = ds.variables[var]
                # dims (tuple), shape (tuple), dtype
                dims = getattr(da, "dims", ())
                shape = getattr(da, "shape", None)
                dtype = getattr(getattr(da, "values", None), "dtype", None)
                kind = getattr(dtype, "kind", None) if dtype is not None else None
                is_coord = var in ds.coords
                # selected attrs (file-provided)
                attrs = getattr(da, "attrs", {}) or {}
                units = attrs.get("units", None)
                calendar = attrs.get("calendar", None)
                axis = attrs.get("axis", None)
                standard_name = attrs.get("standard_name", None)
                long_name = attrs.get("long_name", None)

                logger.info(
                    f"{prefix}  VAR '{var}': dims={dims}, shape={shape}, dtype={dtype}, kind={kind}, coord={is_coord}"
                )
                logger.info(
                    f"{prefix}    attrs: units={units!r}, calendar={calendar!r}, axis={axis!r}, "
                    f"standard_name={standard_name!r}, long_name={long_name!r}"
                )
            except Exception as e:
                logger.info(f"{prefix}  Could not inspect variable '{var}': {e}")

        # If netCDF4 backend available, list its variables and key attributes too (without loading arrays)
        nc_obj = getattr(getattr(ds, "_file_obj", None), "ds", None)
        if nc_obj is not None:
            try:
                nc_vars = list(nc_obj.variables.keys())
                logger.info(f"{prefix}Underlying netCDF4 variables ({len(nc_vars)}): {nc_vars[:100]}")
                # show a few of them with attributes
                for nc_name in nc_vars[:50]:
                    try:
                        ncvar = nc_obj.variables[nc_name]
                        nc_dims = getattr(ncvar, "dimensions", None)
                        nc_shape = getattr(ncvar, "shape", None)
                        nc_units = getattr(ncvar, "units", None)
                        nc_cal = getattr(ncvar, "calendar", None)
                        nc_axis = getattr(ncvar, "axis", None)
                        nc_std = getattr(ncvar, "standard_name", None)
                        nc_long = getattr(ncvar, "long_name", None)
                        logger.info(
                            f"{prefix}  netCDF VAR '{nc_name}': dims={nc_dims}, shape={nc_shape}, "
                            f"units={nc_units!r}, calendar={nc_cal!r}, axis={nc_axis!r}, "
                            f"standard_name={nc_std!r}, long_name={nc_long!r}"
                        )
                    except Exception:
                        continue
            except Exception as e_nc:
                logger.info(f"{prefix}  Could not inspect underlying netCDF4 object: {e_nc}")

    except Exception as e:
        logger.error(f"{prefix}Failed to summarize dataset: {e}", exc_info=True)


def parse_xarray_timestamp(
    ts_slice: xr.Dataset, time_coord: str, index: int, allow_fallback: bool = False
) -> Optional[datetime]:
    """
    Parse a single timestamp from an xarray Dataset slice, with detailed logging.

    Behavior:
      - Prefer values already decoded by xarray (datetime64, pandas.Timestamp, python datetime).
      - If raw numeric values are present, attempt to decode using the file-provided
        time variable metadata via netCDF4.num2date (units & calendar) when available.
      - If units/calendar are not present and `allow_fallback` is True, do a controlled
        numeric fallback (short heuristics) and return the best plausible result.
      - Always logs:
          * raw value and dtype
          * if a netCDF variable was consulted: its 'units' and 'calendar'
          * which decode method was used and the final decoded timestamp (ISO, tz-aware)
      - Returns a timezone-aware (UTC) `datetime.datetime` when possible, otherwise None.

    Notes:
      - This function is defensive: it will NOT guess an epoch unless explicitly allowed
        via fallback. It will record and log the decision path.
      - `index` is used only when consulting the underlying netCDF variable directly.
        When `ts_slice` already contains the time coordinate (already indexed), the
        function will use that value and will NOT re-index the underlying nc variable.

    Args:
        ts_slice: A dataset slice obtained via `ds.isel({time_dim: i})` or similar.
        time_coord: Name of the time coordinate/variable to inspect.
        index: The index along the dataset's time dimension (0-based). Used when reading
               from the underlying netCDF variable.
        allow_fallback: If True, allow numeric heuristics fallback when no units/calendar
                        info exists in the file. Default False.

    Returns:
        timezone-aware UTC datetime or None on failure.
    """
    try:
        logger.info(
            f"parse_xarray_timestamp: attempting to parse time_coord='{time_coord}' at index={index} (allow_fallback={allow_fallback})"
        )

        # 1) Try to get time value directly from the provided ts_slice (preferred).
        raw_present = False
        raw_val = None
        raw_dtype = None
        source = "none"

        try:
            if time_coord in ts_slice:
                # ts_slice[time_coord] may be scalar (0-d) or small array
                arr = ts_slice[time_coord].values
                # normalize scalars and 1-element arrays to a python scalar
                if np.isscalar(arr):
                    raw_val = arr
                else:
                    arr_np = np.asarray(arr)
                    if arr_np.size == 1:
                        raw_val = arr_np.flatten()[0]
                    else:
                        # multi-element - this is unexpected for a time coordinate in a sliced timeslice
                        raw_val = arr_np
                raw_dtype = getattr(getattr(ts_slice[time_coord], "values", None), "dtype", None)
                raw_present = True
                source = "xarray_value"
                logger.info(
                    f"parse_xarray_timestamp: found time_coord in slice; raw_dtype={raw_dtype}, raw_val_sample={repr(raw_val)}"
                )
        except Exception as e:
            logger.info(f"parse_xarray_timestamp: error while reading time_coord from slice: {e}")

        # 2) If not present in slice (or we want to consult file-level metadata), try underlying netCDF variable
        nc_obj = getattr(getattr(ts_slice, "_file_obj", None), "ds", None)
        nc_var = None
        nc_units = None
        nc_calendar = None
        if not raw_present and nc_obj is not None:
            try:
                if time_coord in nc_obj.variables:
                    nc_var = nc_obj.variables[time_coord]
                    # attempt to read single value at index, but defensively (do not read whole var)
                    try:
                        if getattr(nc_var, "ndim", 1) > 0:
                            # index may be valid for the global variable
                            time_num = nc_var[index]
                        else:
                            time_num = nc_var[0]
                        raw_val = time_num
                        raw_dtype = getattr(getattr(nc_var, "dtype", None), "kind", None) or type(time_num).__name__
                        raw_present = True
                        source = "netcdf_variable"
                    except Exception as e_index:
                        logger.info(f"parse_xarray_timestamp: cannot read nc variable at index {index}: {e_index}")
                    nc_units = getattr(nc_var, "units", None)
                    nc_calendar = getattr(nc_var, "calendar", None)
                    logger.info(
                        f"parse_xarray_timestamp: underlying netCDF var attributes: units={nc_units!r}, calendar={nc_calendar!r}"
                    )
            except Exception as e:
                logger.info(f"parse_xarray_timestamp: error inspecting underlying netCDF variable: {e}")

        if not raw_present:
            logger.warning(
                f"parse_xarray_timestamp: time coordinate '{time_coord}' not found in slice or underlying file at index {index}"
            )
            return None

        # 3) Interpret raw_val depending on its type
        # Handle already decoded datetimes
        try:
            # pandas.Timestamp -> python datetime
            if isinstance(raw_val, pd.Timestamp):
                dt = raw_val.to_pydatetime()
                if dt.tzinfo is None:
                    dt = pd.Timestamp(dt).tz_localize("UTC").to_pydatetime()
                logger.info(f"parse_xarray_timestamp: decoded (pandas.Timestamp) -> {dt.isoformat()}")
                return dt

            # python datetime
            if isinstance(raw_val, datetime):
                dt = raw_val
                # ensure timezone-aware in UTC
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                logger.info(f"parse_xarray_timestamp: decoded (datetime) -> {dt.isoformat()}")
                return dt

            # numpy datetime64
            if isinstance(raw_val, np.datetime64):
                dt = pd.Timestamp(raw_val).to_pydatetime()
                if getattr(dt, "tzinfo", None) is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                logger.info(f"parse_xarray_timestamp: decoded (numpy.datetime64) -> {dt.isoformat()}")
                return dt

            # cftime-like objects: try to extract fields
            # Do not assume timezone; localize to UTC
            if hasattr(raw_val, "year") and hasattr(raw_val, "month") and hasattr(raw_val, "day"):
                try:
                    dt = datetime(
                        int(raw_val.year),
                        int(raw_val.month),
                        int(raw_val.day),
                        int(getattr(raw_val, "hour", 0)),
                        int(getattr(raw_val, "minute", 0)),
                        int(getattr(raw_val, "second", 0)),
                        int(getattr(raw_val, "microsecond", 0)),
                        tzinfo=timezone.utc,
                    )
                    logger.info(f"parse_xarray_timestamp: decoded (cftime-like) -> {dt.isoformat()}")
                    return dt
                except Exception as e_cftime:
                    logger.info(f"parse_xarray_timestamp: failed to convert cftime-like object: {e_cftime}")

            # Strings: attempt ISO-parsing
            if isinstance(raw_val, (str, bytes)) or (
                isinstance(raw_val, np.ndarray) and raw_val.dtype.kind in ("U", "S", "O")
            ):
                try:
                    # bytes -> decode
                    s = raw_val.decode() if isinstance(raw_val, (bytes, bytearray)) else str(raw_val)
                    parsed = pd.to_datetime([s], errors="coerce")[0]
                    if not pd.isna(parsed):
                        dt = parsed.to_pydatetime()
                        if dt.tzinfo is None:
                            dt = pd.Timestamp(dt).tz_localize("UTC").to_pydatetime()
                        logger.info(f"parse_xarray_timestamp: decoded (string) -> {dt.isoformat()} (from '{s}')")
                        return dt
                except Exception as e_str:
                    logger.info(f"parse_xarray_timestamp: string parsing failed: {e_str}")

            # Numeric: consult units/calendar if present in nc_var (preferred), else optionally fallback
            if isinstance(raw_val, (float, int, np.floating, np.integer, np.ndarray)):
                # If we have a netCDF variable with units, preferentially use num2date
                if nc_var is not None and nc_units:
                    try:
                        from netCDF4 import num2date

                        # num2date supports scalar or array-like
                        decoded = num2date(raw_val, nc_units, calendar=nc_calendar)
                        # num2date may return scalar or array; normalize
                        if isinstance(decoded, (list, tuple, np.ndarray)):
                            decoded = decoded[0] if len(decoded) > 0 else None

                        if decoded is None:
                            logger.info("parse_xarray_timestamp: netCDF num2date returned None")
                            # fallthrough to optional fallback
                        else:
                            # convert decoded (may be datetime or cftime) to python datetime with UTC tz
                            if isinstance(decoded, datetime):
                                dt = decoded
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=timezone.utc)
                                else:
                                    dt = dt.astimezone(timezone.utc)
                                logger.info(
                                    f"parse_xarray_timestamp: decoded (num2date using file units) -> {dt.isoformat()} (units: {nc_units})"
                                )
                                return dt
                            # cftime-like
                            if hasattr(decoded, "year"):
                                try:
                                    dt = datetime(
                                        int(decoded.year),
                                        int(decoded.month),
                                        int(decoded.day),
                                        int(getattr(decoded, "hour", 0)),
                                        int(getattr(decoded, "minute", 0)),
                                        int(getattr(decoded, "second", 0)),
                                        int(getattr(decoded, "microsecond", 0)),
                                        tzinfo=timezone.utc,
                                    )
                                    logger.info(
                                        f"parse_xarray_timestamp: decoded (num2date->cftime) -> {dt.isoformat()} (units: {nc_units})"
                                    )
                                    return dt
                                except Exception as e_dec:
                                    logger.info(f"parse_xarray_timestamp: converting num2date(cftime) failed: {e_dec}")

                    except Exception as e_num:
                        logger.info(f"parse_xarray_timestamp: netCDF4.num2date failed: {e_num}")

                # If no units/calendar present and fallbacks allowed, attempt numeric heuristics
                if allow_fallback:
                    try:
                        # Reuse the fallback orchestrator from file_parser (if present)
                        # We expect _attempt_numeric_time_conversions to exist in the same module.
                        arr = np.asarray(raw_val).reshape(-1)
                        decoded_arr, diag = _attempt_numeric_time_conversions(
                            arr, plausible_year_min=1950, plausible_year_max=2050
                        )
                        logger.info(f"parse_xarray_timestamp: fallback diagnostics: {diag}")
                        if decoded_arr is not None and len(decoded_arr) > 0:
                            # decoded_arr is numpy datetime64/DatetimeIndex -> pick first element
                            dt_np = decoded_arr[0]
                            # convert numpy datetime64 to python datetime
                            dt = pd.Timestamp(dt_np).to_pydatetime()
                            if dt.tzinfo is None:
                                dt = pd.Timestamp(dt).tz_localize("UTC").to_pydatetime()
                            logger.info(f"parse_xarray_timestamp: decoded (numeric fallback) -> {dt.isoformat()}")
                            return dt
                    except Exception as e_fb:
                        logger.info(f"parse_xarray_timestamp: fallback numeric decoding failed: {e_fb}")

                # If numeric and no method produced a datetime, log and return None
                logger.warning(
                    f"parse_xarray_timestamp: numeric raw value present but could not be decoded safely (source={source}, nc_units={nc_units}). Value sample: {repr(raw_val)}"
                )
                return None

            # If we reach here, the raw_val type was not handled
            logger.warning(
                f"parse_xarray_timestamp: unsupported raw value type for time coordinate '{time_coord}': {type(raw_val)}; sample: {repr(raw_val)}"
            )
            return None

        except Exception as e_inner:
            logger.warning(
                f"parse_xarray_timestamp: unexpected error while interpreting raw value: {e_inner}", exc_info=True
            )
            return None

    except Exception as e_outer:
        logger.warning(
            f"parse_xarray_timestamp: failed for coord '{time_coord}' at index {index}: {e_outer}", exc_info=True
        )
        return None


def _decode_time_variable(ds: xr.Dataset, time_coord: str):
    """
    Decode a time variable from an xarray Dataset *using only metadata contained in the file*.

    This helper will:
    - Prefer to use the dataset's underlying netCDF4 variable (if available) and call
      netCDF4.num2date with the variable's own 'units' and 'calendar' attributes.
    - If netCDF4 backend is not available, attempt to use xarray's existing values (if already decoded).
    - Return an array-like object with decoded datetimes. The result may be:
        - numpy.datetime64 array
        - numpy.object_ array of Python datetimes or cftime objects
    - Return None on failure.

    Args:
        ds: xarray.Dataset instance that contains the time coordinate.
        time_coord: Name of the time coordinate variable.

    Returns:
        Decoded array-like of datetime-like objects or None.
    """
    # Try to use xarray provided values first (safe: uses file metadata if xarray has already decoded)
    try:
        if time_coord in ds:
            vals = ds[time_coord].values
            # If xarray already gave datetime64 or object array with datetimes, return that directly
            if vals is not None:
                if vals.dtype.kind == "M" or vals.dtype == object:
                    return vals
    except Exception:
        # continue to underlying netCDF fallback
        pass

    # Fallback: attempt to access the underlying netCDF4.Dataset if available
    nc_obj = getattr(getattr(ds, "_file_obj", None), "ds", None)

    if nc_obj is None:
        # Try alternative path: some backends expose file object differently; attempt ds.encoding
        try:
            backend = ds.encoding.get("source", None)
        except Exception:
            backend = None

    if nc_obj is not None:
        if time_coord not in nc_obj.variables:
            logger.info(f"Time coordinate '{time_coord}' not present in underlying netCDF4 variables.")
            return None

        tvar = nc_obj.variables[time_coord]
        # read raw numeric/string data
        try:
            raw = tvar[:]
        except Exception as e_raw:
            logger.info(f"Failed to read raw data for time variable '{time_coord}': {e_raw}")
            return None

        units = getattr(tvar, "units", None)
        calendar = getattr(tvar, "calendar", None)

        # If units attribute is present and looks like a time-units string (file-provided), use num2date
        if units is not None:
            try:
                # import locally to avoid hard dependency if not used elsewhere
                from netCDF4 import num2date

                decoded = num2date(raw, units=units, calendar=calendar)
                # num2date returns scalar or array-like (including cftime objects). Normalize to numpy array.
                return np.array(decoded)
            except Exception as e_num:
                logger.info(f"netCDF4.num2date failed for variable '{time_coord}': {e_num}")
                # continue to other fallbacks

        # If the variable contains strings representing ISO timestamps, try pandas parsing (best-effort)
        try:
            if raw.dtype.kind in ("U", "S", "O"):
                # Use pandas to attempt robust parsing; this is still reading only file contents.
                parsed = pd.to_datetime(raw, errors="coerce")
                if parsed.notna().any():
                    return parsed.to_numpy()
        except Exception as e_parse:
            logger.info(f"String parsing of time variable '{time_coord}' failed: {e_parse}")

        # If no method succeeded, return the raw data as object array (so caller can inspect)
        try:
            return np.array(raw)
        except Exception:
            return None

    # If no backend and xarray didn't decode, attempt to coerce whatever xarray has
    try:
        if time_coord in ds:
            vals = ds[time_coord].values
            # attempt pandas parsing as last resort
            try:
                parsed = pd.to_datetime(vals, errors="coerce")
                if parsed.notna().any():
                    return parsed.to_numpy()
            except Exception:
                return vals
    except Exception:
        pass

    return None


def _find_time_dimension(ds: xr.Dataset) -> Tuple[Optional[str], Optional[str]]:
    """
    Identify a time coordinate and its dimension using only metadata contained in the file.

    This function inspects xarray variables/coords and, if available, the underlying netCDF4
    variables. It produces detailed logs explaining why candidate variables are scored and
    which candidate was finally selected.

    Returns:
        (time_dim_name_or_None, time_coord_name_or_None)
    """
    try:
        logger.info("Attempting to find time dimension: scanning xarray coords for datetime64 types.")
        # quick check: coords already datetime dtype
        for coord in ds.coords:
            try:
                arr = ds.coords[coord].values
                dtype = getattr(arr, "dtype", None)
                if dtype is not None and getattr(dtype, "kind", None) == "M":
                    dims = ds.coords[coord].dims
                    time_dim = dims[0] if dims else coord
                    logger.info(f"Found time coordinate '{coord}' as datetime64 in coords (dims={dims}).")
                    return time_dim, coord
            except Exception:
                continue
    except Exception:
        logger.info("Exception while scanning coords for datetime64; falling back to variable inspection.")

    logger.info("Scanning variables for time-candidate attributes (units/calendar/axis/standard_name/long_name).")

    nc_obj = getattr(getattr(ds, "_file_obj", None), "ds", None)

    candidates: List[Tuple[str, int, dict]] = []  # (name, score, details)

    # Inspect xarray variables first (attributes come from the file)
    for var_name, da in ds.variables.items():
        try:
            score = 0
            details = {"source": "xarray", "reasons": []}
            # dtype check
            dtype = getattr(getattr(da, "values", None), "dtype", None)
            if dtype is not None and getattr(dtype, "kind", None) == "M":
                score += 90
                details["reasons"].append("xarray-decoded-datetime")
            attrs = getattr(da, "attrs", {}) or {}
            units = attrs.get("units", "")
            if isinstance(units, str) and "since" in units.lower():
                score += 100
                details["reasons"].append(f"units='{units}'")
            if "calendar" in attrs:
                score += 50
                details["reasons"].append(f"calendar='{attrs.get('calendar')}'")
            axis = attrs.get("axis", "")
            if isinstance(axis, str) and axis.upper() == "T":
                score += 40
                details["reasons"].append(f"axis='T'")
            std_name = attrs.get("standard_name", "")
            if isinstance(std_name, str) and "time" in std_name.lower():
                score += 30
                details["reasons"].append(f"standard_name='{std_name}'")
            long_name = attrs.get("long_name", "")
            if isinstance(long_name, str) and "time" in long_name.lower():
                score += 20
                details["reasons"].append(f"long_name='{long_name}'")
            if var_name in ds.coords:
                score += 10
                details["reasons"].append("is_coordinate")

            if score > 0:
                details.update(
                    {"units": units, "calendar": attrs.get("calendar", None), "axis": axis, "dtype": str(dtype)}
                )
                candidates.append((var_name, score, details))
                logger.info(f"Candidate (xarray) '{var_name}': score={score}, details={details}")
        except Exception as e_var:
            logger.info(f"Error inspecting variable '{var_name}': {e_var}")

    # Inspect netCDF4 variables explicitly if available
    if nc_obj is not None:
        try:
            for var_name in nc_obj.variables:
                try:
                    ncvar = nc_obj.variables[var_name]
                    # skip if already in candidates (we may have added from xarray)
                    if any(var_name == c[0] for c in candidates):
                        continue
                    score = 0
                    details = {"source": "netcdf4", "reasons": []}
                    units = getattr(ncvar, "units", "")
                    if isinstance(units, str) and "since" in units.lower():
                        score += 100
                        details["reasons"].append(f"units='{units}'")
                    if hasattr(ncvar, "calendar"):
                        score += 50
                        details["reasons"].append(f"calendar='{getattr(ncvar, 'calendar')}'")
                    axis = getattr(ncvar, "axis", "")
                    if isinstance(axis, str) and axis.upper() == "T":
                        score += 40
                        details["reasons"].append("axis='T'")
                    std = getattr(ncvar, "standard_name", "")
                    if isinstance(std, str) and "time" in std.lower():
                        score += 30
                        details["reasons"].append(f"standard_name='{std}'")
                    long = getattr(ncvar, "long_name", "")
                    if isinstance(long, str) and "time" in long.lower():
                        score += 20
                        details["reasons"].append(f"long_name='{long}'")

                    if score > 0:
                        dims = getattr(ncvar, "dimensions", None)
                        details.update({"units": units, "calendar": getattr(ncvar, "calendar", None), "dims": dims})
                        candidates.append((str(var_name), score, details))
                        logger.info(f"Candidate (netCDF) '{var_name}': score={score}, details={details}")
                except Exception:
                    continue
        except Exception:
            logger.info("Error iterating netCDF4 variables for candidacy.")

    if not candidates:
        logger.error("No candidate time variables found in file metadata.")
        return None, None

    # Sort by score descending and log top candidates
    candidates.sort(key=lambda x: x[1], reverse=True)
    logger.info("Time variable candidates ranked (top 10):")
    for name, score, details in candidates[:10]:
        logger.info(f"  {name}: score={score}, details={details}")

    best_var, best_score, best_details = candidates[0]
    logger.info(f"Selected time variable candidate '{best_var}' with score {best_score} (details: {best_details})")

    # Determine associated dimension name from xarray if available, else from netCDF4 variable dims
    try:
        if best_var in ds.variables:
            dims = ds[best_var].dims
            time_dim = dims[0] if dims else best_var
            return time_dim, best_var
        # else fall back to netCDF object
        nc_obj = getattr(getattr(ds, "_file_obj", None), "ds", None)
        if nc_obj is not None and best_var in nc_obj.variables:
            dims = nc_obj.variables[best_var].dimensions
            time_dim = dims[0] if dims else best_var
            return time_dim, best_var
    except Exception as e:
        logger.error(f"Error determining dimension for selected time variable '{best_var}': {e}")

    return None, None


def _handle_nd_datafile(
    file_path_obj: Path, variables_to_parse: List[str], allow_time_fallback: bool = False
) -> Tuple[
    Optional[xr.Dataset],
    Optional[Dict[str, Any]],
    Optional[str],
    Optional[str],
]:
    """
    Open and prepare an N-dimensional .nc file using xarray, while explicitly decoding
    the time coordinate using only file-contained metadata. Optionally performs a
    controlled fallback if no file-provided time metadata exists.

    Args:
        file_path_obj: Path to the .nc file.
        variables_to_parse: List of variable names to extract metadata for (empty -> all).
        allow_time_fallback: If True, allow the fallback numeric heuristics (isolated) when
                             no file metadata exists for time decoding. Default False.

    Returns:
        (xr.Dataset or None, metadata dict or None, time_dim name or None, time_coord name or None)
    """
    ds: Optional[xr.Dataset] = None
    file_metadata: Optional[Dict[str, Any]] = None
    time_dim: Optional[str] = None
    time_coord: Optional[str] = None

    if file_path_obj.suffix.lower() != ".nc":
        logger.warning(f"File {file_path_obj.name} is not .nc and failed native sunpy parsing. Skipping.")
        return None, None, None, None

    try:
        # Open with decode_times=False to explicitly control decoding using only file-provided metadata
        try:
            ds = xr.open_dataset(file_path_obj, engine="h5netcdf", decode_times=False)
            used_engine = "h5netcdf"
        except Exception:
            logger.info("'h5netcdf' engine failed. Trying 'netcdf4' engine.")
            ds = xr.open_dataset(file_path_obj, engine="netcdf4", decode_times=False)
            used_engine = "netcdf4"

        logger.info(f"Opened {file_path_obj.name} with xarray (decode_times=False) using engine={used_engine}.")

        # produce dataset summary for diagnostics (your existing _log_dataset_summary)
        _log_dataset_summary(ds, file_path_obj=file_path_obj)

        time_dim, time_coord = _find_time_dimension(ds)

        if not time_dim or not time_coord:
            logger.error(
                f"Cannot process N-D file {file_path_obj.name}: No time dim/coord identified from file metadata."
            )
            # STILL extract metadata so that caller can inspect file structure
            file_metadata = _get_xarray_file_metadata(ds, variables_to_parse)
            file_metadata = file_metadata or {}
            file_metadata.setdefault("time_decode_diagnostics", {"found": False, "reason": "no_time_variable_found"})
            # Optionally try fallback if explicitly allowed
            if allow_time_fallback:
                logger.info("No time candidate found via metadata — attempting fallback decoding as permitted.")
                fb_decoded, fb_diag = _fallback_decode_time(ds, time_dim="time", allow_attempt=True)
                file_metadata["time_decode_diagnostics"]["fallback_attempt"] = fb_diag
                if fb_decoded is not None:
                    # assign fallback decoded times as coordinate "time_fallback"
                    try:
                        ds = ds.assign_coords(
                            {"time_fallback": xr.DataArray(fb_decoded, dims=("time",), name="time_fallback")}
                        )
                        file_metadata["time_decode_diagnostics"]["fallback_assigned_coordinate"] = "time_fallback"
                        logger.info("Fallback decode produced candidate times and assigned 'time_fallback' coordinate.")
                        return ds, file_metadata, "time", "time_fallback"
                    except Exception as e_assign:
                        logger.warning(f"Failed to assign fallback decoded coordinate: {e_assign}")
                        file_metadata["time_decode_diagnostics"]["fallback_assign_error"] = str(e_assign)
                return None, file_metadata, None, None
            # no fallback allowed
            return None, file_metadata, None, None

        # promote data var -> coord if needed
        if time_coord in ds.data_vars and time_coord not in ds.coords:
            logger.info(f"Promoting data variable '{time_coord}' to coordinate.")
            ds = ds.set_coords(time_coord)

        # decode using file metadata
        decoded_times = _decode_time_variable(ds, time_coord)
        diag: Dict[str, Any] = {"found": True}
        if decoded_times is None:
            logger.warning(f"Could not decode times for '{time_coord}' in {file_path_obj.name} using file metadata.")
            diag["decoded"] = False
            # attempt fallback only if permitted by flag
            if allow_time_fallback:
                logger.info("Attempting fallback numeric decoding as allowed by flag.")
                fb_decoded, fb_diag = _fallback_decode_time(ds, time_dim=time_dim, allow_attempt=True)
                diag["fallback"] = fb_diag
                # if fallback produced decoding, assign as new coordinate name "time_fallback"
                if fb_decoded is not None:
                    try:
                        ds = ds.assign_coords(
                            {
                                f"{time_coord}_fallback": xr.DataArray(
                                    fb_decoded, dims=(time_dim,), name=f"{time_coord}_fallback"
                                )
                            }
                        )
                        logger.info(f"Assigned fallback-decoded times as coordinate '{time_coord}_fallback'.")
                        file_metadata = _get_xarray_file_metadata(ds, variables_to_parse)
                        file_metadata.setdefault("time_decode_diagnostics", diag)
                        return ds, file_metadata, time_dim, f"{time_coord}_fallback"
                    except Exception as e_assign:
                        logger.warning(f"Failed to assign fallback-decoded coordinate: {e_assign}")
                        diag["fallback_assign_error"] = str(e_assign)

            # no fallback or fallback failed: return ds with diagnostics for manual inspection
            file_metadata = _get_xarray_file_metadata(ds, variables_to_parse)
            file_metadata.setdefault("time_decode_diagnostics", diag)
            return ds, file_metadata, time_dim, time_coord

        # if decoded_times present: assign/ensure coordinate
        try:
            time_da = xr.DataArray(decoded_times, dims=(time_dim,), coords={time_dim: decoded_times}, name=time_coord)
            ds = ds.assign_coords({time_coord: time_da})
            logger.info(f"Assigned decoded time coordinate '{time_coord}' with dim '{time_dim}'.")
        except Exception as e_assign:
            logger.warning(f"Failed to assign decoded time coordinate to dataset: {e_assign}")

        # extract attributes
        file_metadata = _get_xarray_file_metadata(ds, variables_to_parse)
        file_metadata.setdefault("time_decode_diagnostics", {"found": True, "decoded_using_file_metadata": True})
        return ds, file_metadata, time_dim, time_coord

    except Exception as xr_e:
        logger.error(f"xarray processing failed for {file_path_obj.name}: {xr_e}", exc_info=True)
        if ds:
            ds.close()
        return None, None, None, None


def _get_cdf_file_metadata(cdf_file: CDF, variables_to_parse: List[str]) -> Dict[str, Any]:
    """
    Helper to extract variable attributes from a single CDF file.

    Args:
        cdf_file (CDF): An opened cdflib.CDF object.
        variables_to_parse (List[str]): A specific list of variables to extract.
            If empty, all variables are extracted.

    Returns:
        Dict[str, Any]: A dictionary where keys are variable names and
        values are dictionaries of their attributes.
    """
    cdf_metadata: Dict[str, Any] = {}
    vars_to_process: List[str] = variables_to_parse
    if not vars_to_process:
        vars_to_process = cast(Dict[str, Any], cdf_file.cdf_info()).get("zVariables", [])
        logger.info(f"No variables specified. Discovering all {len(vars_to_process)} variables from CDF.")

    for var in vars_to_process:
        try:
            cdf_metadata[var] = cdf_file.varattsget(var)
        except Exception:
            logger.warning(f"Could not get attributes for variable '{var}'.")
    return cdf_metadata


def _get_xarray_file_metadata(ds: xr.Dataset, variables_to_parse: List[str]) -> Dict[str, Any]:
    """
    Helper to extract variable attributes from an xarray Dataset.

    Args:
        ds (xr.Dataset): The xarray Dataset to parse.
        variables_to_parse (List[str]): A specific list of variables to extract.
            If empty, all data variables are extracted.

    Returns:
        Dict[str, Any]: A dictionary where keys are variable names and
        values are dictionaries of their attributes.
    """
    file_metadata: Dict[str, Any] = {}
    vars_to_process: List[str] = variables_to_parse
    if not vars_to_process:
        vars_to_process = list(ds.data_vars)
        logger.info(f"No variables specified. Discovering all {len(vars_to_process)} variables from xarray Dataset.")

    for var in vars_to_process:
        if var in ds:
            try:
                file_metadata[var] = ds[var].attrs
            except Exception as e_attr:
                logger.warning(f"Could not read attrs for var '{var}': {e_attr}")
    return file_metadata


def _parse_1d_timeseries_file(
    file_path_str: str,
) -> List[GenericTimeSeries]:
    """
    Attempts to parse a file using the native sunpy.timeseries.TimeSeries factory.

    This function handles files that can be loaded as 1D time series (e.g., CSV, some CDFs).

    Args:
        file_path_str (str): The path to the file.

    Returns:
        List[GenericTimeSeries]: A list of valid, non-empty GenericTimeSeries objects
        parsed from the file. Returns an empty list if parsing fails.
    """
    try:
        # TimeSeries factory can return a single instance or a list
        ts_from_file: Union[GenericTimeSeries, List[GenericTimeSeries]] = TimeSeries(file_path_str)
        flat_list: List[Any] = expand_list([ts_from_file])

        valid_ts_list: List[GenericTimeSeries] = []
        for ts in flat_list:
            if isinstance(ts, GenericTimeSeries) and len(ts.to_table()) > 0:
                valid_ts_list.append(ts)

        if valid_ts_list:
            logger.info(f"Successfully parsed {Path(file_path_str).name} with native sunpy parser (1D data).")
        return valid_ts_list

    except NoMatchError:
        logger.info(f"Sunpy parser failed for {Path(file_path_str).name}. Will attempt xarray fallback.")
    except Exception as e:
        logger.error(
            f"Failed to parse data from file {Path(file_path_str).name}: {e}",
            exc_info=True,
        )
    return []


def _concatenate_timeseries(
    ts_list: List[GenericTimeSeries],
) -> Optional[GenericTimeSeries]:
    """
    Combines a list of 1D TimeSeries objects into a single, sorted TimeSeries.

    Args:
        ts_list (List[GenericTimeSeries]): A list of 1D TimeSeries objects
            to combine.

    Returns:
        Optional[GenericTimeSeries]: A single, combined, and sorted
        TimeSeries object, or None if combination fails.
    """
    if not ts_list:
        return None

    logger.info(f"Concatenating {len(ts_list)} 1D-TimeSeries objects...")
    try:
        if len(ts_list) == 1:
            return ts_list[0]

        all_tables: List[Table] = [ts.to_table() for ts in ts_list]
        combined_table: Table = vstack_tables(all_tables)
        combined_table.sort("date")

        # Recreate TimeSeries object
        ts_combined: Union[GenericTimeSeries, List[GenericTimeSeries]] = TimeSeries(combined_table, ts_list[0].meta)

        # Handle potential list return type from TimeSeries factory
        if isinstance(ts_combined, list):
            ts_combined_obj = ts_combined[0] if ts_combined else None
        else:
            ts_combined_obj = ts_combined

        if not ts_combined_obj:
            logger.error(
                "Combination resulted in 'None' or empty object.",
                exc_info=True,
            )
            return None

        return ts_combined_obj

    except Exception as e:
        logger.error(
            f"Failed to concatenate or truncate 1D TimeSeries: {e}",
            exc_info=True,
        )
        return None


def parse_datafiles(
    file_paths: List[str],
    start_time: datetime,
    end_time: datetime,
    variables_to_parse: List[str],
) -> Tuple[
    Optional[Union[GenericTimeSeries, List[Tuple[xr.Dataset, str, str]]]],
    Dict[str, Any],
]:
    """
    Parses downloaded files (CDF, NC) and extracts metadata.

    This function iterates through files, attempts to parse them as 1D
    sunpy TimeSeries, and falls back to parsing them as N-D xarray Datasets.
    It returns either a single combined 1D TimeSeries or a list of N-D
    Datasets.

    Args:
        file_paths (List[str]): List of file paths to parse.
        start_time (datetime): The start time for truncating 1D data.
        end_time (datetime): The end time for truncating 1D data.
        variables_to_parse (List[str]): Specific variables to extract
            metadata for.

    Returns:
        Tuple[
            Optional[Union[GenericTimeSeries, List[Tuple[xr.Dataset, str, str]]]],
            Dict[str, Any]
        ]:
            - A single `GenericTimeSeries` for 1D data.
            - OR a LIST of Tuples [(xr.Dataset, time_dim, time_coord), ...]
              for N-D data.
            - AND the file metadata dictionary.
    """

    logger.info(f"Parsing {len(file_paths)} data files...")

    file_metadata: Dict[str, Any] = {}
    all_1d_timeseries: List[GenericTimeSeries] = []
    all_nd_datasets: List[Tuple[xr.Dataset, str, str]] = []
    has_extracted_metadata = False

    if not file_paths:
        logger.warning("No file paths provided. Skipping parse.")
        return None, file_metadata

    for i, file_path_str in enumerate(file_paths):
        file_path_obj = Path(file_path_str)
        logger.info(f"Processing file ({i + 1}/{len(file_paths)}): {file_path_obj.name}")

        # Metadata extraction for CDF files
        if not has_extracted_metadata and file_path_obj.suffix.lower() == ".cdf":
            try:
                with CDF(file_path_str) as cdf_file:
                    file_metadata = _get_cdf_file_metadata(cdf_file, variables_to_parse)
                has_extracted_metadata = True
                logger.info(f"Extracted CDF attributes from: {file_path_obj.name}")
            except Exception as e:
                logger.warning(f"File {file_path_obj.name} looks like CDF but failed to read attributes: {e}")

        # 1D TimeSeries
        parsed_ts_list = _parse_1d_timeseries_file(file_path_str)

        if parsed_ts_list:
            all_1d_timeseries.extend(parsed_ts_list)
        else:
            # N-D Datafile (NetCDF/xarray)
            (
                nd_data,
                nd_metadata,
                time_dim,
                time_coord,
            ) = _handle_nd_datafile(file_path_obj, variables_to_parse, allow_time_fallback=True)

            if nd_data is not None and time_dim is not None and time_coord is not None:
                all_nd_datasets.append((nd_data, time_dim, time_coord))
            if nd_metadata and not has_extracted_metadata:
                file_metadata = nd_metadata
                has_extracted_metadata = True

    # --- Post-Processing ---

    if all_1d_timeseries:
        # If we found any 1D data, we prioritize it and discard partial N-D results
        for ds, _, _ in all_nd_datasets:
            ds.close()

        ts_combined = _concatenate_timeseries(all_1d_timeseries)
        if not ts_combined:
            return None, file_metadata
        try:
            logger.info(f"Filtering combined 1D TimeSeries to range: {start_time} to {end_time}")
            ts_filtered: GenericTimeSeries = ts_combined.truncate(start_time, end_time)
            logger.info(f"Filtered 1D TimeSeries contains {len(ts_filtered.to_table())} entries.")
            return ts_filtered, file_metadata
        except Exception as e:
            logger.error(f"Failed to truncate TimeSeries: {e}", exc_info=True)
            return ts_combined, file_metadata

    elif all_nd_datasets:
        logger.info(f"Returning a list of {len(all_nd_datasets)} N-D xarray.Dataset objects (not concatenated).")
        return all_nd_datasets, file_metadata

    logger.warning("No 1D or N-D data could be parsed.")
    return None, file_metadata
