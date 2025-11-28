import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, cast

import numpy as np
import xarray as xr
from astropy.table import Table
from astropy.table import vstack as vstack_tables
from cdflib import CDF
from sunpy.timeseries import GenericTimeSeries, TimeSeries
from sunpy.util import expand_list
from sunpy.util.datatype_factory_base import NoMatchError

from space_weather_analysis.util.logging_utils import log_xarray_dataset_summary
from space_weather_analysis.util.time_utils import (
    decode_time_variable_from_file,
    fallback_decode_time,
    find_time_dimension,
)

logger = logging.getLogger(__name__)


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

    This function serves as the main entry point for file processing. It attempts
    to parse files first as 1D SunPy TimeSeries (efficient for simple data) and
    falls back to N-D Xarray Datasets for complex multi-dimensional data.

    Args:
        file_paths (List[str]): List of file paths to parse.
        start_time (datetime): The start time for truncating 1D data.
        end_time (datetime): The end time for truncating 1D data.
        variables_to_parse (List[str]): Specific variables to extract metadata for.

    Returns:
        Tuple: A tuple containing:
            - The parsed data object (either a single GenericTimeSeries or a list of N-D datasets).
            - The extracted file metadata dictionary.
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

        # Extract metadata from CDF files separately as SunPy abstraction might hide it
        if not has_extracted_metadata and file_path_obj.suffix.lower() == ".cdf":
            metadata = _try_extract_cdf_metadata(file_path_str, variables_to_parse)
            if metadata:
                file_metadata = metadata
                has_extracted_metadata = True

        # Parse as 1D TimeSeries (SunPy native)
        parsed_ts_list = _parse_1d_timeseries_file(file_path_str)

        if parsed_ts_list:
            all_1d_timeseries.extend(parsed_ts_list)
        else:
            # Parse as N-D Datafile (NetCDF/Xarray)
            nd_result = _handle_nd_datafile(file_path_obj, variables_to_parse, allow_time_fallback=True)
            nd_data, nd_meta, time_dim, time_coord = nd_result

            if nd_data is not None and time_dim is not None and time_coord is not None:
                all_nd_datasets.append((nd_data, time_dim, time_coord))

            if nd_meta and not has_extracted_metadata:
                file_metadata = nd_meta
                has_extracted_metadata = True

    # Prioritize 1D data if any was found, as it is easier to handle downstream
    if all_1d_timeseries:
        return _finalize_1d_timeseries(all_1d_timeseries, start_time, end_time, all_nd_datasets, file_metadata)

    if all_nd_datasets:
        logger.info(f"Returning a list of {len(all_nd_datasets)} N-D xarray.Dataset objects.")
        return all_nd_datasets, file_metadata

    logger.warning("No 1D or N-D data could be parsed.")
    return None, file_metadata


def _try_extract_cdf_metadata(file_path: str, variables: List[str]) -> Dict[str, Any]:
    """
    Safely extracts attributes from a CDF file using the cdflib library.

    Args:
        file_path (str): Path to the CDF file.
        variables (List[str]): Variables to extract attributes for.

    Returns:
        Dict[str, Any]: Extracted metadata or empty dict on failure.
    """
    try:
        with CDF(file_path) as cdf_file:
            meta = _get_cdf_file_metadata(cdf_file, variables)
            logger.info(f"Extracted CDF attributes from: {Path(file_path).name}")
            return meta
    except Exception as e:
        logger.warning(f"File {Path(file_path).name} looks like CDF but failed to read attributes: {e}")
        return {}


def _finalize_1d_timeseries(
    ts_list: List[GenericTimeSeries],
    start: datetime,
    end: datetime,
    nd_datasets: List[Tuple[xr.Dataset, str, str]],
    metadata: Dict[str, Any],
) -> Tuple[Optional[GenericTimeSeries], Dict[str, Any]]:
    """
    Concatenates and filters 1D timeseries, cleaning up unused N-D datasets.

    Args:
        ts_list (List[GenericTimeSeries]): List of 1D timeseries objects.
        start (datetime): Start time filter.
        end (datetime): End time filter.
        nd_datasets (List): List of open N-D datasets to close.
        metadata (Dict): File metadata.

    Returns:
        Tuple: Combined and truncated TimeSeries object and metadata.
    """
    # Clean up open handles for N-D datasets that won't be used
    for ds, _, _ in nd_datasets:
        ds.close()

    ts_combined = _concatenate_timeseries(ts_list)
    if not ts_combined:
        return None, metadata

    try:
        logger.info(f"Filtering combined 1D TimeSeries to range: {start} to {end}")
        ts_filtered = ts_combined.truncate(start, end)
        logger.info(f"Filtered 1D TimeSeries contains {len(ts_filtered.to_table())} entries.")
        return ts_filtered, metadata
    except Exception as e:
        logger.error(f"Failed to truncate TimeSeries: {e}", exc_info=True)
        return ts_combined, metadata


def _handle_nd_datafile(
    file_path_obj: Path, variables_to_parse: List[str], allow_time_fallback: bool = False
) -> Tuple[Optional[xr.Dataset], Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    Opens and prepares an N-dimensional file using xarray.

    This method orchestrates the opening of the file, logging of its structure,
    identification of the time dimension, and decoding of the time variable.
    It delegates complex time logic to the `time_utils` module.

    Args:
        file_path_obj (Path): Path to the file.
        variables_to_parse (List[str]): List of variables to extract metadata for.
        allow_time_fallback (bool): Whether to allow heuristic time decoding.

    Returns:
        Tuple: (Dataset, Metadata, TimeDimName, TimeCoordName) or (None, None, None, None).
    """
    if file_path_obj.suffix.lower() != ".nc":
        logger.warning(f"File {file_path_obj.name} is not .nc and failed native sunpy parsing. Skipping.")
        return None, None, None, None

    ds = _open_xarray_dataset(file_path_obj)
    if ds is None:
        return None, None, None, None

    # Diagnostic logging
    log_xarray_dataset_summary(ds, file_path_obj=file_path_obj)

    time_dim, time_coord = find_time_dimension(ds)

    if not time_dim or not time_coord:
        return _handle_missing_time_dimension(ds, variables_to_parse, allow_time_fallback)

    # Ensure time variable is a coordinate
    if time_coord in ds.data_vars and time_coord not in ds.coords:
        logger.info(f"Promoting data variable '{time_coord}' to coordinate.")
        ds = ds.set_coords(time_coord)

    # Attempt to decode the time variable using file metadata
    decoded_times = decode_time_variable_from_file(ds, time_coord)
    diag: Dict[str, Any] = {"found": True, "decoded_using_file_metadata": False}

    if decoded_times is not None:
        _assign_decoded_times(ds, time_dim, time_coord, decoded_times)
        diag["decoded_using_file_metadata"] = True
    elif allow_time_fallback:
        logger.info("Attempting fallback numeric decoding as allowed by flag.")
        fb_decoded, fb_diag = fallback_decode_time(ds, time_dim=time_dim, allow_attempt=True)
        diag["fallback"] = fb_diag

        if fb_decoded is not None:
            # Create a new coordinate for fallback times to avoid overwriting original data
            new_coord = f"{time_coord}_fallback"
            _assign_decoded_times(ds, time_dim, new_coord, fb_decoded)
            time_coord = new_coord

    file_metadata = _get_xarray_file_metadata(ds, variables_to_parse)
    file_metadata.setdefault("time_decode_diagnostics", diag)

    return ds, file_metadata, time_dim, time_coord


def _open_xarray_dataset(file_path: Path) -> Optional[xr.Dataset]:
    """
    Attempts to open a dataset using supported xarray engines.

    Args:
        file_path (Path): Path to the file.

    Returns:
        Optional[xr.Dataset]: The opened dataset or None if all engines fail.
    """
    for engine in ["h5netcdf", "netcdf4"]:
        try:
            # decode_times=False is critical to allow manual inspection/fallback
            ds = xr.open_dataset(file_path, engine=engine, decode_times=False)
            logger.info(f"Opened {file_path.name} with xarray (engine={engine}).")
            return ds
        except Exception:
            continue

    logger.error(f"Failed to open {file_path.name} with supported xarray engines.")
    return None


def _assign_decoded_times(ds: xr.Dataset, dim: str, name: str, values: np.ndarray) -> None:
    """
    Assigns decoded time values as a new coordinate to the dataset.

    Args:
        ds (xr.Dataset): The dataset to modify.
        dim (str): Dimension name.
        name (str): New coordinate name.
        values (np.ndarray): Decoded time values.
    """
    try:
        time_da = xr.DataArray(values, dims=(dim,), coords={dim: values}, name=name)
        ds.assign_coords({name: time_da})
        logger.info(f"Assigned decoded time coordinate '{name}' with dim '{dim}'.")
    except Exception as e:
        logger.warning(f"Failed to assign decoded time coordinate: {e}")


def _handle_missing_time_dimension(
    ds: xr.Dataset, variables: List[str], allow_fallback: bool
) -> Tuple[Optional[xr.Dataset], Dict[str, Any], Optional[str], Optional[str]]:
    """
    Handles cases where no time dimension could be identified from metadata.

    Args:
        ds (xr.Dataset): The dataset.
        variables (List[str]): Variables to parse metadata for.
        allow_fallback (bool): Whether to attempt heuristic decoding.

    Returns:
        Tuple: (Dataset, Metadata, TimeDim, TimeCoord) with failure or fallback results.
    """
    logger.error("No time dim/coord identified from file metadata.")

    file_metadata = _get_xarray_file_metadata(ds, variables)
    file_metadata.setdefault("time_decode_diagnostics", {"found": False, "reason": "no_time_variable_found"})

    if allow_fallback:
        logger.info("Attempting fallback decoding on 'time' dimension assumption.")
        # Heuristic assumption: dimension is named "time"
        fb_decoded, fb_diag = fallback_decode_time(ds, time_dim="time", allow_attempt=True)
        file_metadata["time_decode_diagnostics"]["fallback_attempt"] = fb_diag

        if fb_decoded is not None:
            _assign_decoded_times(ds, "time", "time_fallback", fb_decoded)
            return ds, file_metadata, "time", "time_fallback"

    return None, file_metadata, None, None


def _get_cdf_file_metadata(cdf_file: CDF, variables_to_parse: List[str]) -> Dict[str, Any]:
    """
    Extracts variable attributes from a CDF file object.

    Args:
        cdf_file (CDF): Opened CDF file.
        variables_to_parse (List[str]): List of variables (empty for all).

    Returns:
        Dict[str, Any]: Mapping of variable names to attributes.
    """
    cdf_metadata: Dict[str, Any] = {}
    vars_to_process = variables_to_parse

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
    Extracts variable attributes from an xarray Dataset.

    Args:
        ds (xr.Dataset): The dataset.
        variables_to_parse (List[str]): List of variables (empty for all).

    Returns:
        Dict[str, Any]: Mapping of variable names to attributes.
    """
    file_metadata: Dict[str, Any] = {}
    vars_to_process = variables_to_parse

    if not vars_to_process:
        vars_to_process = list(ds.data_vars)
        logger.info(f"No variables specified. Discovering all {len(vars_to_process)} variables from xarray Dataset.")

    for var in vars_to_process:
        if var in ds:
            try:
                file_metadata[var] = ds[var].attrs
            except Exception as e:
                logger.warning(f"Could not read attrs for var '{var}': {e}")

    return file_metadata


def _parse_1d_timeseries_file(file_path_str: str) -> List[GenericTimeSeries]:
    """
    Attempts to parse a file as a 1D timeseries using SunPy.

    Args:
        file_path_str (str): Path to the file.

    Returns:
        List[GenericTimeSeries]: List of successfully parsed timeseries objects.
    """
    try:
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
    Combines multiple 1D TimeSeries objects into a single sorted TimeSeries.

    Args:
        ts_list (List[GenericTimeSeries]): List of timeseries to combine.

    Returns:
        Optional[GenericTimeSeries]: The combined timeseries or None on failure.
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

        ts_combined: Union[GenericTimeSeries, List[GenericTimeSeries]] = TimeSeries(combined_table, ts_list[0].meta)

        if isinstance(ts_combined, list):
            return ts_combined[0] if ts_combined else None
        return ts_combined

    except Exception as e:
        logger.error(f"Failed to concatenate 1D TimeSeries: {e}", exc_info=True)
        return None
