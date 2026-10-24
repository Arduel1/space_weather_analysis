import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from hapiclient.hapitime import hapitime2datetime
from sunpy.timeseries import GenericTimeSeries

logger = logging.getLogger(__name__)


def convert_hapi_to_timeseries(data: np.ndarray, meta: Dict[str, Any]) -> Optional[GenericTimeSeries]:
    """
    Converts raw HAPI data and metadata into a SunPy GenericTimeSeries object.

    This function orchestrates the conversion process:
    - Extracts and parses the time stamps using HAPI-specific logic.
    - Iterates through all parameters defined in the metadata.
    - Handles both scalar values and vector/array values (expanding them).
    - Combines everything into a single pandas DataFrame.
    - Wraps the result in a GenericTimeSeries.

    Args:
        data: The raw data as a structured numpy array returned by `hapi()`.
        meta: The metadata dictionary returned by `hapi()`.

    Returns:
        The constructed TimeSeries object, or None if conversion fails
        or data is empty.
    """
    # Early exit for empty or invalid input
    if data is None or meta is None or len(data) == 0:
        logger.warning("Cannot convert empty or None data to TimeSeries.")
        return None

    try:
        # hapitime2datetime robustly handles HAPI ISO8601 strings
        time_values = hapitime2datetime(data["Time"])
        time_index = pd.Index(time_values, name="Time")

        # Collect partial DataFrames for each parameter to concat efficiently at the end
        data_frames: List[pd.DataFrame] = []

        parameters = meta.get("parameters", [])
        if not parameters:
            logger.warning("HAPI metadata contains no 'parameters' definition.")
            return None

        for param in parameters:
            param_name = param.get("name")

            # Skip the time parameter (already index) or parameters not present in data
            if param_name == "Time" or param_name not in (data.dtype.names or ()):
                continue

            raw_values = data[param_name]
            param_size = param.get("size")

            if param_size:
                # Handle Array/Vector Parameters (expand to multiple columns)
                df_part = _expand_array_parameter(param_name, raw_values, param, time_index)
                if df_part is not None:
                    data_frames.append(df_part)
            else:
                # Handle Scalar Parameters
                df_part = pd.DataFrame({param_name: raw_values}, index=time_index)
                data_frames.append(df_part)

        if not data_frames:
            logger.warning("No valid data columns found after processing parameters.")
            return None

        final_df = pd.concat(data_frames, axis=1)
        return GenericTimeSeries(final_df, meta)

    except Exception as e:
        logger.error(f"Failed to convert HAPI data to TimeSeries: {e}", exc_info=True)
        return None


def _expand_array_parameter(
    param_name: str,
    param_data: np.ndarray,
    param_meta: Dict[str, Any],
    time_index: pd.Index,
) -> Optional[pd.DataFrame]:
    """
    Expands a multi-dimensional HAPI parameter (array/vector) into a DataFrame.

    This helper handles the generation of column names for array parameters based
    on labels provided in the metadata or strictly numerical indices if labels
    are missing.

    Args:
        param_name: The name of the parameter.
        param_data: The numpy array containing the data for this parameter.
            Expected shape is (n_samples, vector_length).
        param_meta: The metadata dictionary for this parameter.
        time_index: The shared time index for the resulting DataFrame.

    Returns:
        A DataFrame with columns for each component of the array,
        or None if the 'size' attribute is missing or the data shape does not match it.
    """
    # Safety check for size attribute
    size_list = param_meta.get("size")
    if not size_list:
        logger.warning(f"Parameter '{param_name}' is marked as an array but is missing the 'size' attribute.")
        return None

    # We assume 1D arrays for size (vectors), effectively ignoring multi-dim tensors
    vector_length = size_list[0]

    # Validate data shape matches metadata expectation
    if param_data.ndim != 2 or param_data.shape[1] != vector_length:
        logger.warning(
            f"Shape mismatch for parameter '{param_name}'. Expected {vector_length} columns based on metadata, but got shape {param_data.shape}."
        )
        return None

    # Generate meaningful column names
    labels = param_meta.get("label")
    col_names: List[str]

    if isinstance(labels, list) and len(labels) == vector_length:
        # One column '<param_name>_<label>' per provided label
        col_names = [f"{param_name}_{label}" for label in labels]
    else:
        # Fall back to one column '<param_name>_<index>' per component
        col_names = [f"{param_name}_{i}" for i in range(vector_length)]

    return pd.DataFrame(param_data, index=time_index, columns=col_names)
