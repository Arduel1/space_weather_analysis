import logging
from collections import Counter
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from space_weather_analysis.metadata.istp_attributes import map_columns_to_istp_variables

logger = logging.getLogger(__name__)


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
