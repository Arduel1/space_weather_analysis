import logging
import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from hapiclient.hapitime import hapitime2datetime
from sunpy.timeseries import GenericTimeSeries

logger = logging.getLogger(__name__)

# Some HAPI servers write the string "null" where the specification expects a JSON null.
HAPI_NULL_STRING = "null"

# Arrays with more elements are maps, images or spectra rather than single values over time, so they are not stored as columns.
MAX_ARRAY_COLUMNS = 10

NUMERIC_PARAMETER_TYPES = ("double", "integer")
TEXT_PARAMETER_TYPES = ("string", "isotime")


def null_string_to_none(value: Any) -> Any:
    """Maps the string "null" to None and returns any other value unchanged.

    Args:
        value: A metadata value as reported by a HAPI server.

    Returns:
        None if the value is the string "null", otherwise the value itself.
    """
    if value == HAPI_NULL_STRING:
        return None
    return value


def parameters_too_large_for_columns(meta: Dict[str, Any]) -> List[str]:
    """Returns the names of the array parameters with more than MAX_ARRAY_COLUMNS elements.

    Args:
        meta: The metadata dictionary returned by `hapi()`.

    Returns:
        The names of the parameters that are left out of the converted data.
    """
    too_large_names = []
    for param in meta.get("parameters", [])[1:]:
        size = param.get("size")
        if size and math.prod(size) > MAX_ARRAY_COLUMNS:
            too_large_names.append(param.get("name"))
    return too_large_names


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

    This helper handles the generation of column names for array parameters. A
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

    col_names: List[str]
    if len(element_shape) == 1:
        col_names = _vector_column_names(param_name, param_meta.get("label"), element_shape[0])
    else:
        # np.ndindex yields the indices in C order, the same order as the reshape below
        col_names = [_indexed_column_name(param_name, element_index) for element_index in np.ndindex(element_shape)]

    flat_data = param_data.reshape(len(param_data), -1)
    return pd.DataFrame(flat_data, index=time_index, columns=col_names)


def _vector_column_names(param_name: str, labels: Any, vector_length: int) -> List[str]:
    """Builds the column names of a vector parameter.

    Args:
        param_name: The name of the parameter.
        labels: The 'label' attribute of the parameter, which names each component if it is a list of matching length.
        vector_length: The number of components.

    Returns:
        One name '<param_name>_<label>' per label, or '<param_name>_<index>' per component if no usable labels are given.
    """
    if isinstance(labels, list) and len(labels) == vector_length:
        return [f"{param_name}_{label}" for label in labels]
    return [_indexed_column_name(param_name, (index,)) for index in range(vector_length)]


def _indexed_column_name(param_name: str, element_index: tuple) -> str:
    """Builds the column name '<param_name>_<i>_<j>...' of one array element.

    Args:
        param_name: The name of the parameter.
        element_index: The index of the element in each dimension.

    Returns:
        The parameter name followed by each index, joined by underscores.
    """
    index_suffix = "_".join(str(index) for index in element_index)
    return f"{param_name}_{index_suffix}"


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
