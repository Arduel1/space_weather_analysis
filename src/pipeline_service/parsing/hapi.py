import logging
from typing import Any

import numpy as np
import pandas as pd
from hapiclient.hapitime import hapitime2datetime

from space_weather_analysis.util.error_handling import DataProcessingError
from space_weather_analysis.util.hapi_parameters import null_string_to_none, parameter_columns, parameters_too_large_for_columns

logger = logging.getLogger(__name__)

NUMERIC_PARAMETER_TYPES = ("double", "integer")
TEXT_PARAMETER_TYPES = ("string", "isotime")


def convert_hapi_to_frame(data: np.ndarray, meta: dict[str, Any]) -> pd.DataFrame:
    """Converts the data of a HAPI response into a frame with one column per stored field, indexed by time.

    The time stamps come from the time parameter, which HAPI defines as the first parameter. Scalar
    parameters become one column, array parameters one column per element; arrays with more than
    MAX_ARRAY_COLUMNS elements are left out (see parameters_too_large_for_columns). Fill values become
    missing values.

    Args:
        data: The data as structured numpy array returned by `hapi()`.
        meta: The /info response returned by `hapi()`.

    Returns:
        The frame, indexed by the time stamps.

    Raises:
        DataProcessingError: If the response has no parameters, its time values cannot be read, or no parameter yields a column.
    """
    parameters = meta.get("parameters")
    if not parameters:
        raise DataProcessingError("The HAPI response defines no parameters.")

    time_parameter_name = parameters[0].get("name")
    try:
        time_values = hapitime2datetime(data[time_parameter_name])
    except (ValueError, TypeError, KeyError) as error:
        raise DataProcessingError(f"The time values of the HAPI time parameter '{time_parameter_name}' cannot be read: {error}") from error
    time_index = pd.Index(time_values, name=time_parameter_name)

    too_large_names = parameters_too_large_for_columns(meta)
    data_frames: list[pd.DataFrame] = []
    for parameter in parameters[1:]:
        parameter_name = parameter.get("name")
        if parameter_name not in (data.dtype.names or ()) or parameter_name in too_large_names:
            continue
        raw_values = data[parameter_name]
        if parameter.get("size"):
            parameter_frame = _expand_array_parameter(parameter_name, raw_values, parameter, time_index)
            if parameter_frame is None:
                continue
        else:
            parameter_frame = pd.DataFrame({parameter_name: raw_values}, index=time_index)
        data_frames.append(_replace_fill_values(parameter_frame, parameter))

    if not data_frames:
        raise DataProcessingError("No parameter of the HAPI response yields a stored column.")
    return pd.concat(data_frames, axis=1)


def _expand_array_parameter(parameter_name: str, values: np.ndarray, parameter: dict[str, Any], time_index: pd.Index) -> pd.DataFrame | None:
    """Expands an array parameter into one column per element.

    The column names come from parameter_columns, shared with the dataset metadata: a vector gets one
    column per component, named by its labels or by indices, and an array with more than one dimension
    one column per element, named by its indices with the last index varying fastest.

    Args:
        parameter_name: The name of the parameter.
        values: The values of the parameter, shaped (records, *size).
        parameter: The definition of the parameter from the /info response, with its 'size'.
        time_index: The time index of the frame.

    Returns:
        The columns of the elements, or None if the shape of the values does not match the 'size'; the
        parameter is then left out, as its elements cannot be assigned.
    """
    element_shape = tuple(parameter["size"])
    if values.shape[1:] != element_shape:
        logger.warning("Parameter '%s' is left out: its values have the shape %s, but its 'size' is %s.", parameter_name, values.shape, element_shape)
        return None
    column_names = [column.name for column in parameter_columns(parameter)]
    return pd.DataFrame(values.reshape(len(values), -1), index=time_index, columns=column_names)


def _replace_fill_values(frame: pd.DataFrame, param_meta: dict[str, Any]) -> pd.DataFrame:
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


def _replace_fill_in_column(column: pd.Series, param_type: str | None, fill: str | None) -> pd.Series:
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
            logger.warning("Ignoring non-numeric fill value '%s' of numeric column '%s'.", fill, column.name)
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
