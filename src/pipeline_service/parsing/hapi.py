import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from hapiclient.hapitime import hapitime2datetime
from sunpy.timeseries import GenericTimeSeries

from space_weather_analysis.util.hapi_parameters import null_string_to_none, parameter_columns, parameters_too_large_for_columns

logger = logging.getLogger(__name__)

NUMERIC_PARAMETER_TYPES = ("double", "integer")
TEXT_PARAMETER_TYPES = ("string", "isotime")


def convert_hapi_to_timeseries(data: np.ndarray, meta: Dict[str, Any]) -> Optional[GenericTimeSeries]:
    """Converts raw HAPI data and metadata into a SunPy GenericTimeSeries object.

    This function orchestrates the conversion process:
    - Extracts and parses the time stamps of the time parameter, which HAPI defines as the first parameter.
    - Iterates through all other parameters defined in the metadata.
    - Handles both scalar values and array values (expanding them to one column per element).
    - Leaves out arrays with more than MAX_ARRAY_COLUMNS elements (see parameters_too_large_for_columns).
    - Replaces fill values with missing values.
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
        parameters = meta.get("parameters", [])
        if not parameters:
            logger.warning("HAPI metadata contains no 'parameters' definition.")
            return None

        time_parameter_name = parameters[0].get("name")

        # hapitime2datetime robustly handles HAPI ISO8601 strings
        time_values = hapitime2datetime(data[time_parameter_name])
        time_index = pd.Index(time_values, name=time_parameter_name)

        # Collect partial DataFrames for each parameter to concat efficiently at the end
        data_frames: List[pd.DataFrame] = []

        too_large_names = parameters_too_large_for_columns(meta)

        for param in parameters[1:]:
            param_name = param.get("name")

            # Skip parameters not present in data and arrays too large for columns
            if param_name not in (data.dtype.names or ()) or param_name in too_large_names:
                continue

            raw_values = data[param_name]
            param_size = param.get("size")

            if param_size:
                # Handle Array Parameters (expand to multiple columns)
                df_part = _expand_array_parameter(param_name, raw_values, param, time_index)
                if df_part is None:
                    continue
            else:
                # Handle Scalar Parameters
                df_part = pd.DataFrame({param_name: raw_values}, index=time_index)

            data_frames.append(_replace_fill_values(df_part, param))

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
    """Expands a multi-dimensional HAPI parameter (array/vector) into a DataFrame.

    The column names come from parameter_columns, shared with the dataset metadata. A
    vector gets one column per component, named by the labels provided in the
    metadata or by numerical indices if labels are missing. An array with more
    than one dimension gets one column per element, named by its indices in the
    order of the HAPI 'size' attribute, where the last index varies fastest.

    Args:
        param_name: The name of the parameter.
        param_data: The numpy array containing the data for this parameter.
            Expected shape is (n_samples, *size).
        param_meta: The metadata dictionary for this parameter.
        time_index: The shared time index for the resulting DataFrame.

    Returns:
        A DataFrame with columns for each element of the array,
        or None if the 'size' attribute is missing or the data shape does not match it.
    """
    # Safety check for size attribute
    size_list = param_meta.get("size")
    if not size_list:
        logger.warning(f"Parameter '{param_name}' is marked as an array but is missing the 'size' attribute.")
        return None

    element_shape = tuple(size_list)

    # Validate data shape matches metadata expectation
    if param_data.shape[1:] != element_shape:
        logger.warning(
            f"Shape mismatch for parameter '{param_name}'. Expected element shape {element_shape} based on metadata, "
            f"but got data shape {param_data.shape}."
        )
        return None

    col_names = [column.name for column in parameter_columns(param_meta)]

    flat_data = param_data.reshape(len(param_data), -1)
    return pd.DataFrame(flat_data, index=time_index, columns=col_names)


def _replace_fill_values(frame: pd.DataFrame, param_meta: Dict[str, Any]) -> pd.DataFrame:
    """Replaces the fill values of one parameter with missing values.

    HAPI applies the fill value to each element of an array parameter, so every
    column of the frame is compared with the same fill value. Fill values are a
    documented convention, so replacing them is not logged.

    Args:
        frame: The columns of the parameter, one per array element.
        param_meta: The metadata dictionary for this parameter.

    Returns:
        A frame with the same columns, missing where the value equals the fill value.
    """
    param_type = param_meta.get("type")
    fill = null_string_to_none(param_meta.get("fill"))
    fill_text = None if fill is None else str(fill)

    replaced_columns = {}
    for column_name in frame.columns:
        replaced_columns[column_name] = _replace_fill_in_column(frame[column_name], param_type, fill_text)
    return pd.DataFrame(replaced_columns, index=frame.index)


def _replace_fill_in_column(column: pd.Series, param_type: Optional[str], fill: Optional[str]) -> pd.Series:
    """Replaces the values equal to the fill value in one column with missing values.

    Numeric values are compared numerically and text values (string and isotime)
    after stripping surrounding whitespace. Integer columns use the nullable Int64
    type, so the values that are kept stay integers. Text values delivered as bytes
    are decoded, so the documents hold strings.

    Args:
        column: The values of one parameter or array element.
        param_type: The HAPI type of the parameter.
        fill: The fill value as given in the metadata, or None if the parameter has none.

    Returns:
        The column with missing values in place of fill values.
    """
    if param_type == "integer":
        column = column.astype("Int64")
    elif param_type in TEXT_PARAMETER_TYPES:
        column = column.map(_decode_if_bytes)

    if fill is None:
        return column

    if param_type in NUMERIC_PARAMETER_TYPES:
        try:
            fill_number = float(fill)
        except ValueError:
            logger.warning(f"Ignoring non-numeric fill value '{fill}' of numeric column '{column.name}'.")
            return column
        # A fill of NaN matches nothing, as NaN values are already missing
        return column.mask(column == fill_number)

    if param_type in TEXT_PARAMETER_TYPES:
        return column.mask(column.str.strip() == fill.strip())

    return column


def _decode_if_bytes(value: Any) -> Any:
    """Decodes a bytes value as UTF-8 and returns any other value unchanged.

    Args:
        value: One text value as returned by `hapi()`, which delivers isotime values as bytes.

    Returns:
        The decoded string, or the value itself if it is not bytes.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value
