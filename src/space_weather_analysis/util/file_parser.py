import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Hashable, List, Optional, Tuple, Union, cast

import numpy as np
import pandas as pd
import xarray as xr
from astropy.table import Table
from astropy.table import vstack as vstack_tables
from astropy.time import Time
from cdflib import CDF
from sunpy.timeseries import GenericTimeSeries, TimeSeries
from sunpy.util import expand_list
from sunpy.util.datatype_factory_base import NoMatchError

logger = logging.getLogger(__name__)


def parse_xarray_timestamp(ts_slice: xr.Dataset, time_coord: str, index: int) -> Optional[datetime]:
    """
    Safely parses the timestamp from an xarray slice.

    This function attempts to convert the timestamp from various formats (Unix timestamp,
    scalar object, numpy datetime64) into a standard Python datetime object.

    Args:
        ts_slice (xr.Dataset): The single time-slice of the dataset.
        time_coord (str): The name of the time coordinate.
        index (int): The current loop index (used for logging context).

    Returns:
        Optional[datetime]: The parsed datetime object or None if parsing fails.
    """
    try:
        time_val: Any = ts_slice[time_coord].values

        # Try conversion via Astropy Time (handles Unix/Encoded formats robustly)
        timestamp_obj: Any = Time(time_val, format="unix").to_datetime()

        if isinstance(timestamp_obj, datetime):
            return timestamp_obj

        # Handle 0-d arrays/scalars (often returned by xarray slices)
        if hasattr(timestamp_obj, "item"):
            timestamp_item = timestamp_obj.item()
            if isinstance(timestamp_item, datetime):
                return timestamp_item

        # Check for masked values (missing data)
        if np.ma.is_masked(timestamp_obj):
            logger.warning(f"Skipping timestep {index}: Time value is masked.")
            return None

        logger.warning(f"Timestamp at index {index} is not a scalar datetime ({type(timestamp_obj)}).")
        return None

    except Exception as e:
        # If Astropy fails, fallback or just log error
        logger.warning(f"Skipping timestep {index}, timestamp parse failed for coord '{time_coord}': {e}")
        return None


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


def _find_time_dimension(
    ds: xr.Dataset,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Robustly finds the time dimension and coordinate in an xarray.Dataset.

    It checks both coordinates and data variables for time-like data,
    first by checking dtype, then by falling back to common names like 'Epoch'.

    Args:
        ds (xr.Dataset): The xarray Dataset to search.

    Returns:
        Tuple[Optional[str], Optional[str]]: A tuple of
        (time_dimension_name, time_coordinate_name), or (None, None)
        if not found.
    """
    time_coord_name: Optional[str] = None

    # Combine coords and data_vars for a complete search
    all_vars: Dict[Hashable, xr.DataArray] = {**ds.coords, **ds.data_vars}
    possible_coords: List[str] = []

    # Check dtype for datetime compatibility
    for var_name, var in all_vars.items():
        if pd.api.types.is_datetime64_any_dtype(var.dtype):
            possible_coords.append(str(var_name))

    if possible_coords:
        time_coord_name = possible_coords[0]  # Take the first valid one
        if "Epoch" in possible_coords:  # Prefer 'Epoch' if available as it is standard in space physics
            time_coord_name = "Epoch"
        logger.info(f"Found time coordinate via dtype: {time_coord_name}")

    # Fallback to names if dtype check fails
    if not time_coord_name:
        fallback_names = ["Epoch", "time", "Time"]
        for name in fallback_names:
            if name in all_vars:
                time_coord_name = name
                logger.info(f"Found time coordinate via fallback name: {time_coord_name}")
                break

    if not time_coord_name:
        logger.error(
            "Could not find any time-like coordinate. "
            f"Coords: {list(ds.coords.keys())}, "
            f"Data Vars: {list(ds.data_vars.keys())}"
        )
        return None, None

    try:
        time_var: xr.DataArray = (
            ds.coords[time_coord_name] if time_coord_name in ds.coords else ds.data_vars[time_coord_name]
        )
        time_dims = time_var.dims
    except KeyError:
        logger.error(f"Identified time coord '{time_coord_name}' but could not access it.")
        return None, None

    if len(time_dims) == 0:
        logger.error(f"Time coordinate '{time_coord_name}' is a scalar (0 dimensions).")
        return None, None

    time_dim_name: str = str(time_dims[0])

    if len(time_dims) > 1:
        logger.warning(
            f"Time coordinate '{time_coord_name}' has multiple dims: {time_dims}. "
            f"Assuming '{time_dim_name}' is the time dimension for iteration."
        )

    if time_dim_name not in ds.dims:
        logger.error(
            f"Time coordinate '{time_coord_name}' depends on dim '{time_dim_name}', "
            f"which is not in dataset dims {list(ds.dims.keys())}."
        )
        return None, None

    logger.info(f"Identified time dimension: '{time_dim_name}' (using coordinate: '{time_coord_name}')")
    return time_dim_name, time_coord_name


def _handle_nd_datafile(
    file_path_obj: Path, variables_to_parse: List[str]
) -> Tuple[
    Optional[xr.Dataset],
    Optional[Dict[str, Any]],
    Optional[str],
    Optional[str],
]:
    """
    Handles N-dimensional data files (typically .nc) using xarray.

    Opens the dataset, attempts to find time dimensions, decodes time,
    and extracts metadata.

    Args:
        file_path_obj (Path): The path to the .nc file.
        variables_to_parse (List[str]): A specific list of variables to
            extract metadata for.

    Returns:
        Tuple[Optional[xr.Dataset], Optional[Dict[str, Any]],
              Optional[str], Optional[str]]:
            - The opened xr.Dataset (or None on failure).
            - The extracted file metadata (or None).
            - The identified time dimension name (or None).
            - The identified time coordinate name (or None).
    """
    ds: Optional[xr.Dataset] = None
    file_metadata: Optional[Dict[str, Any]] = None
    time_dim: Optional[str] = None
    time_coord: Optional[str] = None

    # Only attempt to parse NetCDF via xarray if suffix matches
    if file_path_obj.suffix.lower() != ".nc":
        logger.warning(f"File {file_path_obj.name} is not .nc and failed native sunpy parsing. Skipping.")
        return None, None, None, None

    try:
        # Open dataset WITHOUT automatic time decoding first
        try:
            ds = xr.open_dataset(file_path_obj, engine="h5netcdf", decode_times=False)
        except Exception:
            logger.info("'h5netcdf' engine failed. Trying 'netcdf4'...")
            ds = xr.open_dataset(file_path_obj, engine="netcdf4", decode_times=False)

        logger.info(f"Successfully opened {file_path_obj.name} with xarray (lazy load, times not decoded).")

        time_dim, time_coord = _find_time_dimension(ds)

        if not time_dim or not time_coord:
            logger.error(f"Cannot process N-D file {file_path_obj.name}: No time dim/coord found.")
            if ds:
                ds.close()
            return None, None, None, None

        if time_coord in ds.data_vars:
            logger.info(f"Promoting data variable '{time_coord}' to a coordinate.")
            ds = ds.set_coords(time_coord)

        logger.info("Applying CF time decoding...")
        try:
            ds = xr.decode_cf(ds)
            logger.info(f"Time coordinate '{time_coord}' dtype after decode: {ds[time_coord].dtype}")
        except Exception as e_decode:
            logger.warning(f"CF time decoding failed: {e_decode}. Will proceed with raw time values.")

        # Re-check dimensions after decoding
        time_dim, time_coord = _find_time_dimension(ds)
        if not time_dim or not time_coord:
            logger.error("Failed to find time dimension after CF-decoding attempt.")
            if ds:
                ds.close()
            return None, None, None, None

        file_metadata = _get_xarray_file_metadata(ds, variables_to_parse)
        logger.info(f"Extracted xarray attributes from: {file_path_obj.name}")

        return ds, file_metadata, time_dim, time_coord

    except Exception as xr_e:
        logger.error(
            f"xarray/Dask processing failed for {file_path_obj.name}: {xr_e}",
            exc_info=True,
        )
        if ds:
            ds.close()
        return None, None, None, None


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
            ) = _handle_nd_datafile(file_path_obj, variables_to_parse)

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
