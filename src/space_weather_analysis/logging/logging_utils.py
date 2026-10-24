import logging
from pathlib import Path
from typing import Any, Hashable, List, Optional

import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)


def log_xarray_dataset_summary(ds: xr.Dataset, file_path_obj: Optional[Path] = None) -> None:
    """
    Logs a compact, safe summary of an xarray Dataset without loading large data arrays.

    This function inspects dimensions, coordinates, variables, and attributes
    to provide diagnostic information useful for debugging parsing issues. It is
    designed to be fail-safe, ensuring that logging errors do not crash the
    main execution flow.

    Args:
        ds (xr.Dataset): An xarray.Dataset (opened but not fully loaded).
        file_path_obj (Optional[Path]): Optional Path for contextual logging (filename).
    """
    prefix = ""
    if file_path_obj is not None:
        prefix = f"{file_path_obj.name} - "

    try:
        logger.info(f"{prefix}Dataset summary: dims={dict(ds.dims)}")

        # Log coordinates summary
        coord_names = list(ds.coords)
        logger.info(f"{prefix}Coordinates ({len(coord_names)}): {coord_names}")

        for coord in coord_names:
            _log_coordinate_details(ds, coord, prefix)

        # Log variables summary
        var_names = list(ds.variables)
        logger.info(f"{prefix}All variables ({len(var_names)}): {var_names}")

        for var in var_names:
            _log_variable_details(ds, var, prefix)

        # Log underlying netCDF4 details if available
        _log_netcdf_backend_details(ds, prefix)

    except Exception as e:
        logger.error(f"{prefix}Failed to summarize dataset: {e}", exc_info=True)


def _log_coordinate_details(ds: xr.Dataset, coord: Hashable, prefix: str) -> None:
    """
    Logs details (dimensions, dtype, length) of a specific coordinate in the dataset.

    Args:
        ds (xr.Dataset): The dataset containing the coordinate.
        coord (Hashable): The name/key of the coordinate.
        prefix (str): A prefix string for log messages.
    """
    try:
        da = ds.coords[coord]
        # Access dtype directly from DataArray to avoid loading data via .values
        dtype = getattr(da, "dtype", None)
        kind = None
        if dtype is not None:
            kind = getattr(dtype, "kind", None)

        length: Optional[int] = None
        if getattr(da, "sizes", None):
            length = int(np.prod(list(da.sizes.values())))

        logger.info(f"{prefix}  COORD '{coord}': dims={da.dims}, dtype={dtype}, kind={kind}, length={length}")

        # Log small sample only if length is small to avoid console flooding
        if length is not None and length <= 8:
            _log_small_sample(da, prefix)

    except Exception as e:
        logger.warning(f"{prefix}  Could not inspect coord '{coord}': {e}", exc_info=True)


def _log_small_sample(da: xr.DataArray, prefix: str) -> None:
    """
    Logs a small sample of values from a DataArray.

    It logs at most the first 8 values and is called only for arrays with
    at most 8 elements to provide immediate insight into the data values.

    Args:
        da (xr.DataArray): The DataArray to sample.
        prefix (str): A prefix string for log messages.
    """
    try:
        # Accessing .values here is intentional as we established the array is small
        sample = da.values[:8]
        logger.info(f"{prefix}    sample: {sample}")
    except Exception as e:
        logger.warning(f"{prefix}    Failed to sample data: {e}", exc_info=True)


def _log_variable_details(ds: xr.Dataset, var: Hashable, prefix: str) -> None:
    """
    Logs details (dimensions, shape, dtype, attributes) of a specific variable.

    Args:
        ds (xr.Dataset): The dataset containing the variable.
        var (Hashable): The name/key of the variable.
        prefix (str): A prefix string for log messages.
    """
    try:
        da = ds.variables[var]
        dims = getattr(da, "dims", ())
        shape = getattr(da, "shape", None)

        # Access dtype directly from DataArray/Variable to avoid loading data
        dtype = getattr(da, "dtype", None)
        kind = None
        if dtype is not None:
            kind = getattr(dtype, "kind", None)

        is_coord = var in ds.coords

        # Extract selected attributes that are relevant for time parsing
        attrs = getattr(da, "attrs", {}) or {}
        units = attrs.get("units", None)
        calendar = attrs.get("calendar", None)
        axis = attrs.get("axis", None)
        standard_name = attrs.get("standard_name", None)
        long_name = attrs.get("long_name", None)

        logger.info(f"{prefix}  VAR '{var}': dims={dims}, shape={shape}, dtype={dtype}, kind={kind}, coord={is_coord}")
        logger.info(
            f"{prefix}    attrs: units={units!r}, calendar={calendar!r}, axis={axis!r}, standard_name={standard_name!r}, long_name={long_name!r}"
        )
    except Exception as e:
        logger.warning(f"{prefix}  Could not inspect variable '{var}': {e}", exc_info=True)


def _log_netcdf_backend_details(ds: xr.Dataset, prefix: str) -> None:
    """
    Logs details from the underlying netCDF4 backend object, if accessible.

    This provides a fallback view of the file's metadata as seen by the
    netCDF4 library, which can sometimes differ from xarray's interpretation.

    Args:
        ds (xr.Dataset): The xarray dataset.
        prefix (str): A prefix string for log messages.
    """
    # Access private attributes to get to the backend; this is risky but useful for debugging
    file_obj: Any = getattr(ds, "_file_obj", None)
    nc_obj: Any = getattr(file_obj, "ds", None)

    if nc_obj is None:
        return

    try:
        nc_vars: List[str] = list(nc_obj.variables.keys())
        logger.info(f"{prefix}Underlying netCDF4 variables ({len(nc_vars)}): {nc_vars[:100]}")

        # Show a subset of variables with their raw attributes
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
            except Exception as e:
                logger.warning(f"{prefix}    Could not inspect netCDF variable '{nc_name}': {e}", exc_info=True)
                continue
    except Exception as e_nc:
        logger.warning(f"{prefix}  Could not inspect underlying netCDF4 object: {e_nc}", exc_info=True)
