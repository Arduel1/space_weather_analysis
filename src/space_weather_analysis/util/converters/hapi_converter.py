import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from hapiclient.hapitime import hapitime2datetime

from sunpy.timeseries.timeseriesbase import GenericTimeSeries

logger = logging.getLogger(__name__)


def _process_array_parameter(
    param_data: np.ndarray,
    param_meta: Dict[str, Any],
    time_index: pd.Index,
) -> Optional[pd.DataFrame]:
    """
    Converts a HAPI array parameter (e.g., a vector) into a pandas DataFrame.

    This helper function handles the splitting of the array parameter into
    multiple columns with appropriate labels.

    Args:
        param_data (np.ndarray): The NumPy array data for this parameter.
        param_meta (Dict[str, Any]): The metadata for this specific parameter.
        time_index (pd.Index): The time index to use for the new DataFrame.

    Returns:
        Optional[pd.DataFrame]: A DataFrame containing the split columns,
                                or None if processing fails.
    """
    param_name = param_meta.get("name")
    param_size_list = param_meta.get("size")

    if not param_size_list:
        logger.warning(f"Parameter {param_name} marked as array but has no size.")
        return None

    param_size = param_size_list[0]

    # Validate shape
    if not (len(param_data.shape) == 2 and param_data.shape[1] == param_size):
        logger.warning(f"Shape mismatch for {param_name}. Expected {param_size} columns, got {param_data.shape}.")
        return None

    # Generate column names
    labels = param_meta.get("label")
    col_names: List[str]
    if isinstance(labels, list) and len(labels) == param_size:
        col_names = [f"{param_name}_{label}" for label in labels]
    else:
        col_names = [f"{param_name}_{i}" for i in range(param_size)]

    return pd.DataFrame(param_data, index=time_index, columns=col_names)


def convert_hapi_to_timeseries(data: np.ndarray, meta: Dict[str, Any]) -> Optional[GenericTimeSeries]:
    """
    Converts a HAPI NumPy array and metadata into a sunpy GenericTimeSeries.

    Args:
        data (np.ndarray): The NumPy structured array returned by the client.
        meta (Dict[str, Any]): The metadata dictionary returned by the client.

    Returns:
        Optional[GenericTimeSeries]: A TimeSeries object if conversion
                                     is successful, otherwise None.
    """
    if data is None or meta is None or len(data) == 0:
        logger.warning("Cannot convert empty data to TimeSeries.")
        return None

    try:
        time_values = hapitime2datetime(data["Time"])
        time_index = pd.Index(time_values, name="Time")
        df_main = pd.DataFrame(index=time_index)
        dfs_to_concat: List[pd.DataFrame] = []

        for param in meta.get("parameters", []):
            param_name = param.get("name")
            if param_name == "Time" or param_name not in data.dtype.names:
                continue

            param_data = data[param_name]
            param_size = param.get("size")

            if param_size:
                # Handle array parameters (e.g., vectors)
                df_param = _process_array_parameter(param_data, param, time_index)
                if df_param is not None:
                    dfs_to_concat.append(df_param)
            else:
                # Handle scalar parameters
                df_main[param_name] = param_data

        # Concat all new DataFrames at once
        if dfs_to_concat:
            dfs_to_concat.insert(0, df_main)
            df_main = pd.concat(dfs_to_concat, axis=1)

        # Return the correct class instance
        return GenericTimeSeries(df_main, meta)

    except Exception as e:
        logger.error(f"Failed to convert HAPI data to TimeSeries: {e}", exc_info=True)
        return None
