import logging
from collections import Counter
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from space_weather_analysis.metadata.models import TimestampLocation

logger = logging.getLogger(__name__)

# ISTP names the position of the time stamp in the measurement interval with a number (BIN_LOCATION).
BIN_LOCATIONS: Mapping[float, TimestampLocation] = MappingProxyType(
    {0.0: TimestampLocation.BEGIN, 0.5: TimestampLocation.CENTER, 1.0: TimestampLocation.END}
)
# ISTP assumes the center of the interval if the time variable has no BIN_LOCATION.
DEFAULT_BIN_LOCATION = TimestampLocation.CENTER

# The ISTP variable of a column and the component index within it, None for a 1-D variable.
ColumnSource = tuple[str, int | None]


def map_columns_to_istp_variables(columns: Iterable[Any], variable_attributes: Mapping[str, Any]) -> dict[str, ColumnSource]:
    """Finds the ISTP variable of each column of the delivered data.

    The column of a 1-D variable is named after the variable, the columns of a 2-D variable `{variable}_{i}` for component i.

    Args:
        columns: The column names.
        variable_attributes: The attributes of each delivered variable, keyed by variable name.

    Returns:
        The variable and component index of each column; columns without a matching variable are left out.
    """
    column_sources: dict[str, ColumnSource] = {}
    for column in columns:
        column_name = str(column)
        if column_name in variable_attributes:
            column_sources[column_name] = (column_name, None)
            continue
        variable_name, _, component_text = column_name.rpartition("_")
        if variable_name in variable_attributes and component_text.isdigit():
            column_sources[column_name] = (variable_name, int(component_text))
    return column_sources


def clean_istp_columns(frame: pd.DataFrame, variable_attributes: Mapping[str, Any]) -> pd.DataFrame:
    """Prepares the columns of delivered data for storage following the ISTP Metadata Guidelines.

    The ISTP Metadata Guidelines (International Solar-Terrestrial Physics,
    https://istp-metadata.readthedocs.io/) are the attribute conventions of heliophysics data;
    CDAWeb follows them. Fill values (FILLVAL) become missing values, and values outside
    VALIDMIN/VALIDMAX count as invalid and become missing as well. Fill values are a documented
    convention and are not logged; the number of values out of range is logged once per variable.
    Integer columns use the nullable Int64 type, so the values that are kept stay integers.

    Args:
        frame: The data, one column per 1-D variable or component of a 2-D variable.
        variable_attributes: The attributes of each delivered variable, keyed by variable name.

    Returns:
        The cleaned frame.
    """
    cleaned_frame = frame.copy()
    out_of_range_counts: Counter[str] = Counter()
    for column_name, (variable_name, component_index) in map_columns_to_istp_variables(frame.columns, variable_attributes).items():
        if not pd.api.types.is_numeric_dtype(cleaned_frame[column_name]):
            continue

        attributes = variable_attributes[variable_name]
        column = cleaned_frame[column_name]
        if pd.api.types.is_integer_dtype(column):
            column = column.astype("Int64")

        fill = _component_value(attributes.get("FILLVAL"), component_index)
        if fill is not None:
            column = column.mask(_missing_as_false(column == fill))

        out_of_range = pd.Series(data=False, index=column.index)
        valid_min = _component_value(attributes.get("VALIDMIN"), component_index)
        if valid_min is not None:
            out_of_range |= _missing_as_false(column < valid_min)
        valid_max = _component_value(attributes.get("VALIDMAX"), component_index)
        if valid_max is not None:
            out_of_range |= _missing_as_false(column > valid_max)

        cleaned_frame[column_name] = column.mask(out_of_range)
        out_of_range_counts[variable_name] += int(out_of_range.sum())

    for variable_name, out_of_range_count in out_of_range_counts.items():
        if out_of_range_count:
            logger.warning("Variable '%s': %d values outside VALIDMIN/VALIDMAX are omitted.", variable_name, out_of_range_count)
    return cleaned_frame


def time_stamp_location(variable_attributes: Mapping[str, Any]) -> TimestampLocation:
    """Describes the position of the time stamps in their measurement interval.

    The time variables are the DEPEND_0 targets of the data variables; their BIN_LOCATION attribute gives the position.

    Args:
        variable_attributes: The attributes of each delivered variable, keyed by variable name.

    Returns:
        The position shared by all time variables, 'center' if no time variable gives one, and
        'other' if the time variables differ or one gives a position outside begin, center and end.
    """
    time_variables = set()
    for attributes in variable_attributes.values():
        if isinstance(attributes, dict) and attributes.get("DEPEND_0"):
            time_variables.add(str(attributes["DEPEND_0"]))

    locations = {_bin_location(variable_attributes.get(time_variable, {}).get("BIN_LOCATION")) for time_variable in time_variables}
    if not locations:
        return DEFAULT_BIN_LOCATION
    if len(locations) == 1:
        return locations.pop()
    return TimestampLocation.OTHER


def istp_text_attribute(attributes: Mapping[str, Any], attribute_name: str) -> str | None:
    """Reads a text attribute of an ISTP variable.

    Args:
        attributes: The attributes of the variable.
        attribute_name: The name of the attribute.

    Returns:
        The text without surrounding whitespace, or None if the attribute is missing or blank,
        as ISTP writes a blank UNITS for variables without a unit.
    """
    value = attributes.get(attribute_name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def istp_scalar_attribute(value: Any) -> str | int | float | None:
    """Converts a single-valued ISTP attribute into a plain Python value that BSON can store.

    Args:
        value: The attribute value, a NumPy scalar, a one-element array or text.

    Returns:
        The value as str, int or float, or None if the attribute is missing or holds more than one value.
    """
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value

    values = np.ravel(value)
    if values.size != 1:
        return None
    item = values[0].item() if isinstance(values[0], np.generic) else values[0]
    if isinstance(item, (str, int, float)):
        return item
    return str(item)


def _component_value(attribute_value: Any, component_index: int | None) -> Any:
    """Picks the numeric attribute value that applies to one column.

    ISTP allows one value for all components or one value per component of a vector variable.

    Args:
        attribute_value: The attribute value, a scalar or an array.
        component_index: The component index of the column, None for a 1-D variable.

    Returns:
        The value for the column, or None if the attribute is missing, not numeric or has no value for the component.
    """
    if attribute_value is None:
        return None
    values = np.ravel(attribute_value)
    if values.size == 1:
        value = values[0]
    elif component_index is not None and component_index < values.size:
        value = values[component_index]
    else:
        return None
    if not np.issubdtype(np.asarray(value).dtype, np.number):
        return None
    return value


def _missing_as_false(condition: pd.Series) -> pd.Series:
    """Converts a comparison result into a plain boolean series, False where the compared value is missing.

    Args:
        condition: The result of comparing a column, nullable for Int64 columns.

    Returns:
        A boolean series with the same index.
    """
    return condition.fillna(value=False).astype(bool)


def _bin_location(bin_location: Any) -> TimestampLocation:
    """Names the position given by a BIN_LOCATION attribute.

    Args:
        bin_location: The attribute value, a number between 0 and 1, or None if absent.

    Returns:
        'begin', 'center' or 'end' for 0, 0.5 and 1, 'center' if absent, otherwise 'other'.
    """
    if bin_location is None:
        return DEFAULT_BIN_LOCATION
    try:
        position = float(np.ravel(bin_location)[0])
    except (TypeError, ValueError, IndexError):
        logger.warning("BIN_LOCATION '%s' is not a number; the time stamp location counts as other.", bin_location)
        return TimestampLocation.OTHER
    return BIN_LOCATIONS.get(position, TimestampLocation.OTHER)
