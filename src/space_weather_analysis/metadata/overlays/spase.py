from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any

from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSource, MetadataSourceKind, VariableMetadata, VectorComponent
from space_weather_analysis.metadata.normalize import optional_text, optional_texts

# SPASE (Space Physics Archive Search and Extract, https://spase-group.org/) ranks below curated and ISTP metadata
# and above HAPI and data file metadata, so it only replaces fields that are unset or come from these sources or itself.
REPLACEABLE_SOURCES: frozenset[MetadataSourceKind | None] = frozenset(
    {None, MetadataSourceKind.HAPI, MetadataSourceKind.FILE, MetadataSourceKind.SPASE}
)

# SPASE names the elements of a Cartesian vector by their axis; HAPI names them x, y and z.
CARTESIAN_REPRESENTATION = "Cartesian"
CARTESIAN_COMPONENTS: Mapping[str, VectorComponent] = MappingProxyType(
    {"Component.I": VectorComponent.X, "Component.J": VectorComponent.Y, "Component.K": VectorComponent.Z}
)


def apply_spase_overlay(record: DatasetMetadata, payload: dict[str, Any], retrieved_at: datetime, url: str | None) -> None:
    """Sets the fields the SPASE record gives and no higher-ranked source has set.

    Each record variable whose provider name is the ParameterKey of a SPASE parameter takes description,
    unit, fill value, valid range, element label, coordinate system and vector component from it. The
    cadence, the observed regions, the measurement types and the instruments of the dataset are taken
    from the SPASE record, as no higher-ranked source gives them.

    Args:
        record: The metadata record, changed in place.
        payload: The SPASE record as parsed by the collector: `cadence`, `observed_regions`, `measurement_types`,
            `instrument_ids` and `parameters` keyed by ParameterKey.
        retrieved_at: When the SPASE record was read.
        url: The location the SPASE record was read from.
    """
    parameters = payload.get("parameters")
    if isinstance(parameters, dict):
        for variable in record.variables:
            parameter = parameters.get(variable.source_name)
            if isinstance(parameter, dict):
                _apply_parameter(variable, parameter)

    cadence = optional_text(payload.get("cadence"))
    if cadence:
        record.time.cadence = cadence
    record.observed_regions = optional_texts(payload.get("observed_regions"))
    record.measurement_types = optional_texts(payload.get("measurement_types"))
    record.instrument_ids = optional_texts(payload.get("instrument_ids"))
    record.replace_provenance(MetadataSource(kind=MetadataSourceKind.SPASE, url=url, retrieved_at=retrieved_at))


def _apply_parameter(variable: VariableMetadata, parameter: dict[str, Any]) -> None:
    """Sets the fields of one variable from its SPASE parameter where no higher-ranked source has set them.

    Args:
        variable: The record variable, changed in place.
        parameter: The SPASE parameter with its `elements`.
    """
    element = _element(parameter, variable.element_index)
    element_values = element or {}
    field_values: dict[str, Any] = {
        "description": parameter.get("description"),
        "units": element_values.get("units") or parameter.get("units"),
        "fill": _first_set(element_values.get("fill"), parameter.get("fill")),
        "valid_min": _first_set(element_values.get("valid_min"), parameter.get("valid_min")),
        "valid_max": _first_set(element_values.get("valid_max"), parameter.get("valid_max")),
        "label": element_values.get("name"),
        "coordinate_system": parameter.get("coordinate_system"),
        "vector_component": _vector_component(parameter, element),
    }
    for field_name, value in field_values.items():
        if value is not None and variable.field_sources.get(field_name) in REPLACEABLE_SOURCES:
            setattr(variable, field_name, value)
            variable.field_sources[field_name] = MetadataSourceKind.SPASE


def _element(parameter: dict[str, Any], element_index: int | None) -> dict[str, Any] | None:
    """Finds the SPASE element of the array element a variable holds.

    Args:
        parameter: The SPASE parameter with its `elements` and their 0-based `index`.
        element_index: The flat index of the array element; None for a scalar.

    Returns:
        The element, or None for a scalar or an element the parameter does not describe.
    """
    if element_index is None:
        return None
    for element in parameter.get("elements") or []:
        if isinstance(element, dict) and element.get("index") == element_index:
            return element
    return None


def _vector_component(parameter: dict[str, Any], element: dict[str, Any] | None) -> VectorComponent | None:
    """Names the vector component of an element of a Cartesian vector.

    Args:
        parameter: The SPASE parameter with its coordinate representation.
        element: The SPASE element with its qualifiers; None for a scalar.

    Returns:
        x, y or z, or None for scalars, other representations and elements without axis qualifier.
    """
    if element is None or parameter.get("coordinate_representation") != CARTESIAN_REPRESENTATION:
        return None
    for qualifier in element.get("qualifiers") or []:
        if qualifier in CARTESIAN_COMPONENTS:
            return CARTESIAN_COMPONENTS[qualifier]
    return None


def _first_set(*values: Any) -> Any:
    """Returns the first value that is not None.

    Args:
        *values: The candidate values in order of preference.

    Returns:
        The first set value, or None if none is set.
    """
    return next((value for value in values if value is not None), None)
