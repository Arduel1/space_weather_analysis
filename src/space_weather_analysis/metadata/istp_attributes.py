import logging
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

import numpy as np

from space_weather_analysis.metadata.models import TimestampLocation

logger = logging.getLogger(__name__)

# The ISTP Metadata Guidelines (International Solar-Terrestrial Physics, https://istp-metadata.readthedocs.io/) are the
# attribute conventions of heliophysics data; they name the position of the time stamp in the measurement interval with a number (BIN_LOCATION).
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


def istp_numeric_attribute(value: Any) -> float | None:
    """Reads a single-valued numeric ISTP attribute such as VALIDMIN.

    Args:
        value: The attribute value as delivered with the data.

    Returns:
        The value as float, or None if the attribute is missing, holds one value per component or is not numeric.
    """
    scalar = istp_scalar_attribute(value)
    if isinstance(scalar, (int, float)) and not isinstance(scalar, bool):
        return float(scalar)
    return None


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
