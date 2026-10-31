from datetime import datetime
from typing import Any

import numpy as np

from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSource, MetadataSourceKind, VariableMetadata
from space_weather_analysis.metadata.normalize import normalize_units, optional_text

# The ISTP Metadata Guidelines (International Solar-Terrestrial Physics, https://istp-metadata.readthedocs.io/)
# define the attributes of each CDF variable. CDAWeb publishes them per dataset in a master file, the
# authoritative ISTP metadata of the dataset. Its attribute LABL_PTR_1 names a metadata variable whose
# data (VarData in the master file) holds one label per vector element.
LABEL_POINTER_ATTRIBUTE = "LABL_PTR_1"


def apply_istp_overlay(record: DatasetMetadata, master: dict[str, Any], retrieved_at: datetime, url: str | None) -> None:
    """Sets the variable and dataset fields for which the CDAWeb master file is the authoritative source.

    Each record variable whose provider name is a master variable takes description, label, unit,
    fill value, valid range and variable type from the master attributes where they are set.

    Args:
        record: The metadata record, changed in place.
        master: The master file as parsed by the collector: `globals` and `variables` with their `attributes` and `data`.
        retrieved_at: When the master file was read.
        url: The location the master file was read from.
    """
    master_variables = master.get("variables")
    if isinstance(master_variables, dict):
        for variable in record.variables:
            master_variable = master_variables.get(variable.source_name)
            if isinstance(master_variable, dict):
                _apply_variable_attributes(variable, master_variable, master_variables)

    global_attributes = master.get("globals")
    if isinstance(global_attributes, dict):
        acknowledgement = _global_text(global_attributes.get("Acknowledgement"))
        if acknowledgement:
            record.citation.acknowledgement = acknowledgement
        rules_of_use = _global_text(global_attributes.get("Rules_of_use"))
        if rules_of_use:
            record.citation.rules_of_use = rules_of_use

    record.replace_provenance(MetadataSource(kind=MetadataSourceKind.ISTP, url=url, retrieved_at=retrieved_at))


def _apply_variable_attributes(variable: VariableMetadata, master_variable: dict[str, Any], master_variables: dict[str, Any]) -> None:
    """Sets the fields of one variable from the attributes of its master variable and attributes them to "istp".

    Args:
        variable: The record variable, changed in place.
        master_variable: The master variable with its `attributes` and `description`.
        master_variables: All master variables, which hold the label variables.
    """
    attributes = master_variable.get("attributes") or {}
    element_index = variable.element_index
    scalar_label = optional_text(attributes.get("LABLAXIS")) if element_index is None else None
    field_values = {
        # FIELDNAM is the short name every ISTP variable carries, used when CATDESC is missing
        "description": optional_text(attributes.get("CATDESC")) or optional_text(attributes.get("FIELDNAM")),
        "label": _element_label(master_variable, element_index, master_variables) or scalar_label,
        "units": normalize_units(attributes.get("UNITS")),
        "fill": _scalar_value(attributes.get("FILLVAL")),
        "valid_min": _element_number(attributes.get("VALIDMIN"), element_index),
        "valid_max": _element_number(attributes.get("VALIDMAX"), element_index),
        "var_type": optional_text(attributes.get("VAR_TYPE")),
    }
    for field_name, value in field_values.items():
        if value is not None:
            setattr(variable, field_name, value)
            variable.field_sources[field_name] = MetadataSourceKind.ISTP


def _element_label(master_variable: dict[str, Any], element_index: int | None, master_variables: dict[str, Any]) -> str | None:
    """Reads the label of one vector element from the label variable that LABL_PTR_1 names.

    Args:
        master_variable: The master variable with its `attributes` and `description`.
        element_index: The flat index of the element; None for a scalar.
        master_variables: All master variables, which hold the label variables.

    Returns:
        The label, or None for a scalar, an array with more than one dimension or a missing label.
    """
    if element_index is None:
        return None
    dimension_sizes = (master_variable.get("description") or {}).get("DimSizes")
    if isinstance(dimension_sizes, list) and len(dimension_sizes) != 1:
        return None
    label_variable = master_variables.get(optional_text((master_variable.get("attributes") or {}).get(LABEL_POINTER_ATTRIBUTE)) or "")
    labels = label_variable.get("data") if isinstance(label_variable, dict) else None
    if not isinstance(labels, list) or not 0 <= element_index < len(labels):
        return None
    label = optional_text(labels[element_index])
    return label.strip() if label else None


def _element_number(value: Any, element_index: int | None) -> float | None:
    """Reads a numeric attribute such as VALIDMIN for one element.

    Args:
        value: The attribute value, a single number or one number per element.
        element_index: The flat index of the element; None for a scalar.

    Returns:
        The number of the element, or None if the attribute is missing, not numeric (time ranges are given as text) or does not cover the element.
    """
    if isinstance(value, list):
        elements = np.asarray(value, dtype=object).ravel()
        if element_index is None or not 0 <= element_index < len(elements):
            return None
        value = elements[element_index]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _scalar_value(value: Any) -> str | int | float | None:
    """Reads a single-valued attribute such as FILLVAL.

    Args:
        value: The attribute value.

    Returns:
        The number or text, or None if the attribute is missing, blank or holds several values.
    """
    if isinstance(value, (bool, list)):
        return None
    if isinstance(value, (int, float)):
        return value
    return optional_text(value)


def _global_text(value: Any) -> str | None:
    """Joins a global attribute of one or more entries into one text.

    CDF attributes hold long texts as several entries that continue each other, so the entries are joined by one space.

    Args:
        value: The attribute value, a text or a list of texts.

    Returns:
        The joined text, or None if no entry has text.
    """
    entries = value if isinstance(value, list) else [value]
    texts = [text.strip() for text in (optional_text(entry) for entry in entries) if text]
    return " ".join(texts) or None
