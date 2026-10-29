import math
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import numpy as np

# Arrays with more elements are maps, images or spectra rather than single values over time, so they are not stored as columns.
MAX_ARRAY_COLUMNS = 10

# Some HAPI servers write the string "null" where the specification expects a JSON null.
HAPI_NULL_STRING = "null"


class ParameterColumn(NamedTuple):
    """One stored column of a HAPI parameter.

    HAPI (Heliophysics Application Programmer's Interface, https://hapi-server.org/) describes
    each parameter of a dataset in its /info response; array parameters carry a 'size'.

    Attributes:
        name: The column name, which is the field name in the stored documents.
        element_index: The index of the element in each array dimension, None for a scalar parameter.
        label: The component label used in the column name, None if the name uses indices.
    """

    name: str
    element_index: Optional[Tuple[int, ...]]
    label: Optional[str]


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


def is_too_large_for_columns(parameter: Dict[str, Any]) -> bool:
    """Tells whether an array parameter has more than MAX_ARRAY_COLUMNS elements.

    Args:
        parameter: The definition of one parameter from the HAPI /info response.

    Returns:
        True if the parameter has a 'size' whose element count exceeds MAX_ARRAY_COLUMNS.
    """
    size = parameter.get("size")
    if not size:
        return False
    return math.prod(size) > MAX_ARRAY_COLUMNS


def parameters_too_large_for_columns(meta: Dict[str, Any]) -> List[str]:
    """Returns the names of the array parameters with more than MAX_ARRAY_COLUMNS elements.

    Args:
        meta: The metadata dictionary returned by `hapi()`.

    Returns:
        The names of the parameters that are left out of the converted data.
    """
    too_large_names = []
    for param in meta.get("parameters", [])[1:]:
        if is_too_large_for_columns(param):
            too_large_names.append(param.get("name"))
    return too_large_names


def parameter_columns(parameter: Dict[str, Any]) -> List[ParameterColumn]:
    """Lists the columns a HAPI parameter is stored in.

    A scalar gets one column named after the parameter. A vector gets one column
    per component, named by the labels provided in the metadata or by numerical
    indices if labels are missing. An array with more than one dimension gets one
    column per element, named by its indices in the order of the HAPI 'size'
    attribute, where the last index varies fastest.

    Args:
        parameter: The definition of one parameter from the HAPI /info response.

    Returns:
        The columns in the order of the flattened array elements.
    """
    parameter_name = parameter.get("name")
    size = parameter.get("size")
    if not size:
        return [ParameterColumn(str(parameter_name), None, None)]

    element_shape = tuple(size)
    if len(element_shape) == 1:
        return vector_columns(str(parameter_name), parameter.get("label"), element_shape[0])

    # np.ndindex yields the indices in C order, the order in which the data is flattened
    return [
        ParameterColumn(indexed_column_name(str(parameter_name), element_index), element_index, None) for element_index in np.ndindex(element_shape)
    ]


def vector_columns(parameter_name: str, labels: Any, vector_length: int) -> List[ParameterColumn]:
    """Builds the columns of a vector parameter.

    Args:
        parameter_name: The name of the parameter.
        labels: The 'label' attribute of the parameter, which names each component if it is a list of matching length.
        vector_length: The number of components.

    Returns:
        One column `{parameter_name}_{label}` per label, or `{parameter_name}_{index}` per component if no usable labels are given.
    """
    if isinstance(labels, list) and len(labels) == vector_length:
        return [ParameterColumn(f"{parameter_name}_{label}", (index,), str(label)) for index, label in enumerate(labels)]
    return [ParameterColumn(indexed_column_name(parameter_name, (index,)), (index,), None) for index in range(vector_length)]


def indexed_column_name(parameter_name: str, element_index: Tuple[int, ...]) -> str:
    """Builds the column name `{parameter_name}_{i}_{j}...` of one array element.

    Args:
        parameter_name: The name of the parameter.
        element_index: The index of the element in each dimension.

    Returns:
        The parameter name followed by each index, joined by underscores.
    """
    index_suffix = "_".join(str(index) for index in element_index)
    return f"{parameter_name}_{index_suffix}"
