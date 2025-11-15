import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sunpy.timeseries
import xarray as xr
from astropy.table import Table
from astropy.table import vstack as vstack_tables
from cdflib import CDF
from sunpy.util import expand_list
from sunpy.util.datatype_factory_base import NoMatchError

logger = logging.getLogger(__name__)


def _get_cdf_file_metadata(cdf_file: CDF, variables_to_parse: List[str]) -> Dict[str, Any]:
    """
    Helper to extract variable attributes from a single CDF file.

    Args:
        cdf_file (CDF): An open cdflib.CDF file object.
        variables_to_parse (List[str]): A list of variable names to extract
                                        attributes for.

    Returns:
        Dict[str, Any]: A dictionary where keys are variable names and
                        values are their attributes.
    """
    cdf_metadata = {}
    for var in variables_to_parse:
        try:
            cdf_metadata[var] = cdf_file.varattsget(var)
        except Exception:
            logger.debug(f"Could not get attributes for variable '{var}'.")
    return cdf_metadata


def _get_xarray_file_metadata(ds: xr.Dataset, variables_to_parse: List[str]) -> Dict[str, Any]:
    """
    Helper to extract variable attributes from an xarray Dataset.

    Args:
        ds (xr.Dataset): An open xarray.Dataset object.
        variables_to_parse (List[str]): A list of variable names to extract
                                        attributes for.

    Returns:
        Dict[str, Any]: A dictionary where keys are variable names and
                        values are their attributes.
    """
    file_metadata = {}
    for var in variables_to_parse:
        if var in ds:
            try:
                file_metadata[var] = ds[var].attrs
            except Exception as e_attr:
                logger.warning(f"Could not read attrs for var '{var}': {e_attr}")
    return file_metadata


def _parse_1d_timeseries_file(
    file_path_str: str,
) -> List[sunpy.timeseries.TimeSeries]:
    """
    Attempts to parse a file using the native sunpy.timeseries.TimeSeries
    parser.

    Args:
        file_path_str (str): The string path to the file.

    Returns:
        List[sunpy.timeseries.TimeSeries]: A list of valid TimeSeries objects
                                           (usually one), or an empty
                                           list on failure.
    """
    try:
        ts_from_file = sunpy.timeseries.TimeSeries(file_path_str)
        flat_list = expand_list([ts_from_file])

        valid_ts_list = []
        for ts in flat_list:
            if isinstance(ts, sunpy.timeseries.GenericTimeSeries) and len(ts.to_table()) > 0:
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


def _handle_nd_datafile(file_path_obj: Path, variables_to_parse: List[str]) -> Optional[Dict[str, Any]]:
    """
    Handles N-dimensional data files (typically .nc) using xarray/Dask.

    This function logs the processing steps for N-D data and extracts
    metadata, but does not return TimeSeries data.

    Args:
        file_path_obj (Path): The pathlib.Path object for the file.
        variables_to_parse (List[str]): List of variables to get metadata for.

    Returns:
        Optional[Dict[str, Any]]: File metadata if extracted, else None.
    """
    ds: Optional[xr.Dataset] = None
    file_metadata: Optional[Dict[str, Any]] = None

    if file_path_obj.suffix.lower() != ".nc":
        logger.warning(f"File {file_path_obj.name} is not .nc and failed native sunpy parsing. Skipping.")
        return None

    try:
        # Use chunks for lazy loading via Dask
        try:
            ds = xr.open_dataset(file_path_obj, engine="h5netcdf", chunks={})
        except Exception:
            logger.info("'h5netcdf' engine failed. Trying 'netcdf4'...")
            ds = xr.open_dataset(file_path_obj, engine="netcdf4", chunks={})

        logger.info(f"Successfully opened {file_path_obj.name} with xarray (lazy load).")

        # --- Metadata Extraction (xarray) ---
        file_metadata = _get_xarray_file_metadata(ds, variables_to_parse)
        logger.info(f"Extracted xarray attributes from: {file_path_obj.name}")

        # --- Dask DataFrame Conversion (Lazy) ---
        logger.info("Re-aligning Dask chunks with 'ds.unify_chunks()'...")
        ds = ds.unify_chunks()

        logger.info("Converting xarray.Dataset to dask.DataFrame (lazy operation)...")
        ddf = ds.to_dask_dataframe()

        logger.info(f"Lazy dask.DataFrame created. Partitions: {ddf.npartitions}")
        logger.info(
            "This N-dimensional data is ready for chunk-wise processing (e.g., ddf.map_partitions(...).compute())"
        )
        logger.warning(f"Data from {file_path_obj.name} is N-dimensional. Skipping load into 1D sunpy.timeseries.")
        return file_metadata

    except Exception as xr_e:
        logger.error(
            f"xarray/Dask processing failed for {file_path_obj.name}: {xr_e}",
            exc_info=True,
        )
        return file_metadata  # Return metadata if extracted, even on failure
    finally:
        if ds:
            ds.close()


def _concatenate_timeseries(
    ts_list: List[sunpy.timeseries.TimeSeries],
) -> Optional[sunpy.timeseries.TimeSeries]:
    """
    Combines a list of 1D TimeSeries objects into a single, sorted TimeSeries.

    Args:
        ts_list (List[sunpy.timeseries.TimeSeries]): A list of 1D TimeSeries
                                                     objects to combine.

    Returns:
        Optional[sunpy.timeseries.TimeSeries]: A single, combined and
                                               sorted TimeSeries, or
                                               None if list is empty.
    """
    if not ts_list:
        return None

    logger.info(f"Concatenating {len(ts_list)} 1D-TimeSeries objects...")
    try:
        if len(ts_list) == 1:
            return ts_list[0]

        all_tables: List[Table] = [ts.to_table() for ts in ts_list]
        combined_table = vstack_tables(all_tables)
        combined_table.sort("date")

        ts_combined = sunpy.timeseries.TimeSeries(combined_table, ts_list[0].meta)
        if isinstance(ts_combined, list):
            ts_combined = ts_combined[0] if ts_combined else None

        if not ts_combined:
            logger.error("Combination resulted in 'None' or empty object.", exc_info=True)
            return None

        return ts_combined

    except Exception as e:
        logger.error(f"Failed to concatenate or truncate 1D TimeSeries: {e}", exc_info=True)
        return None


def parse_datafiles(
    file_paths: List[str],
    start_time: datetime,
    end_time: datetime,
    variables_to_parse: List[str],
) -> Tuple[Optional[sunpy.timeseries.TimeSeries], Dict[str, Any]]:
    """
    Parses downloaded files (CDF, NC) and extracts metadata.

    - Attempts to use sunpy.timeseries.TimeSeries for 1D data.
    - Falls back to xarray/Dask for N-dimensional data (like .nc files).
    - Bundles all 1D data into a single, truncated TimeSeries.
    - Extracts variable metadata from the *first* parsable file.

    Args:
        file_paths (List[str]): List of files to parse.
        start_time (datetime): Start time for truncating 1D data.
        end_time (datetime): End time for truncating 1D data.
        variables_to_parse (List[str]): Names of variables from API.

    Returns:
        Tuple[Optional[sunpy.timeseries.TimeSeries], Dict[str, Any]]:
            - A TimeSeries object (for 1D data) or None (for N-D data).
            - A dictionary of file-level variable attributes.
    """
    logger.info(f"Parsing {len(file_paths)} data files...")

    file_metadata: Dict[str, Any] = {}
    all_1d_timeseries: List[sunpy.timeseries.TimeSeries] = []
    has_extracted_metadata = False

    if not file_paths:
        logger.warning("No file paths provided. Skipping parse.")
        return None, file_metadata

    for i, file_path_str in enumerate(file_paths):
        file_path_obj = Path(file_path_str)
        logger.info(f"Processing file ({i + 1}/{len(file_paths)}): {file_path_obj.name}")

        # --- Metadata Extraction (Attempt from CDF) ---
        if not has_extracted_metadata and file_path_obj.suffix.lower() == ".cdf":
            try:
                with CDF(file_path_str) as cdf_file:
                    file_metadata = _get_cdf_file_metadata(cdf_file, variables_to_parse)
                has_extracted_metadata = True
                logger.info(f"Extracted CDF attributes from: {file_path_obj.name}")
            except Exception as e:
                logger.warning(f"File {file_path_obj.name} looks like CDF but failed to read attributes: {e}")

        # --- Data Parsing ---
        parsed_ts_list = _parse_1d_timeseries_file(file_path_str)

        if parsed_ts_list:
            all_1d_timeseries.extend(parsed_ts_list)
        else:
            # If 1D parse failed, attempt N-D (xarray) parse
            nd_metadata = _handle_nd_datafile(file_path_obj, variables_to_parse)
            if nd_metadata and not has_extracted_metadata:
                file_metadata = nd_metadata
                has_extracted_metadata = True

    # --- Bundling (for 1D data only) ---
    if not all_1d_timeseries:
        logger.warning("No 1D-TimeSeries data (e.g., CDFs) could be parsed.")
        return None, file_metadata  # Still return metadata

    ts_combined = _concatenate_timeseries(all_1d_timeseries)

    if not ts_combined:
        return None, file_metadata

    # --- Truncate 1D Data ---
    try:
        logger.info(f"Filtering combined 1D TimeSeries to range: {start_time} to {end_time}")
        ts_filtered = ts_combined.truncate(start_time, end_time)
        logger.info(f"Filtered 1D TimeSeries contains {len(ts_filtered.to_table())} entries.")
        return ts_filtered, file_metadata

    except Exception as e:
        logger.error(f"Failed to truncate TimeSeries: {e}", exc_info=True)
        return ts_combined, file_metadata  # Return un-truncated on error
