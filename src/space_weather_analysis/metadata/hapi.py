import logging
import re
from datetime import datetime
from typing import Any, cast, get_args

import numpy as np

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.metadata.models import (
    CitationInfo,
    DatasetMetadata,
    DataType,
    LinkInfo,
    MetadataSource,
    MetadataSourceKind,
    SkippedParameter,
    SourceInfo,
    TimeInfo,
    TimestampLocation,
    VariableMetadata,
    VectorComponent,
)
from space_weather_analysis.metadata.normalize import normalize_units, optional_text
from space_weather_analysis.util.hapi_parameters import MAX_ARRAY_COLUMNS, ParameterColumn, is_too_large_for_columns, parameter_columns
from space_weather_analysis.util.time_utils import parse_iso_time

logger = logging.getLogger(__name__)

# HAPI parameter types and the value type of the stored field; "float" is a type some servers write in place of "double".
HAPI_DATA_TYPES: dict[str, DataType] = {"double": "double", "float": "double", "integer": "integer", "string": "string", "isotime": "isotime"}

# The HAPI specification defines 'center' as time stamp location if a server gives none.
DEFAULT_TIMESTAMP_LOCATION: TimestampLocation = "center"
TIMESTAMP_LOCATIONS: tuple[str, ...] = get_args(TimestampLocation)
VECTOR_COMPONENTS: tuple[str, ...] = get_args(VectorComponent)

SPASE_ID_PREFIX = "spase://"
# A DOI (Digital Object Identifier, https://www.doi.org/) is "10.", a registrant code, "/" and a suffix,
# optionally written as resolver URL or with the "doi:" prefix.
DOI_PATTERN = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)?(10\.\d{4,9}/\S+)$", re.IGNORECASE)

NOT_STORED_REASON = f"array with more than {MAX_ARRAY_COLUMNS} elements"


def build_hapi_metadata(dataset_id: str, collection: str, info: dict[str, Any], server_url: str | None, retrieved_at: datetime) -> DatasetMetadata:
    """Builds the metadata record of a dataset from the /info response of a HAPI server.

    HAPI (Heliophysics Application Programmer's Interface, https://hapi-server.org/) describes
    a dataset and each of its parameters in the /info response. The variables follow the
    column naming of the HAPI ingestion, so each one names a field of the stored documents.
    The time parameter, the first parameter by HAPI definition, becomes the document time stamp
    and is no variable.

    Args:
        dataset_id: The dataset ID.
        collection: The MongoDB collection holding the data documents.
        info: The /info response as received.
        server_url: The base URL of the HAPI server the info comes from.
        retrieved_at: When the info was read.

    Returns:
        The metadata record; every set variable field is attributed to the source kind "hapi".

    Raises:
        ValueError: If the info has no list of parameters.
    """
    parameters = info.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        raise ValueError(f"HAPI info of '{dataset_id}' has no parameters.")

    variables: list[VariableMetadata] = []
    not_stored: list[SkippedParameter] = []
    for parameter in parameters[1:]:
        if not isinstance(parameter, dict) or not parameter.get("name"):
            logger.warning("HAPI info of '%s': parameter without name ignored: %s", dataset_id, parameter)
            continue
        if is_too_large_for_columns(parameter):
            not_stored.append(SkippedParameter(name=str(parameter["name"]), reason=NOT_STORED_REASON))
            continue
        variables.extend(hapi_parameter_variables(parameter))

    citation_text = optional_text(info.get("datasetCitation")) or optional_text(info.get("citation"))
    resource_id = optional_text(info.get("resourceID"))

    return DatasetMetadata(
        dataset_id=dataset_id,
        collection=collection,
        source=SourceInfo(type=SourceType.HAPI, server_url=server_url),
        title=optional_text(info.get("title")),
        description=optional_text(info.get("description")),
        time=TimeInfo(
            coverage_start=parse_hapi_time(info.get("startDate")),
            coverage_stop=parse_hapi_time(info.get("stopDate")),
            cadence=optional_text(info.get("cadence")),
            timestamp_location=hapi_timestamp_location(info.get("timeStampLocation")),
        ),
        # Some servers give the DOI only as citation, written as doi.org URL
        citation=CitationInfo(
            doi=doi_from_text(resource_id) or doi_from_text(citation_text), text=citation_text, license=license_text(info.get("licenseURL"))
        ),
        links=LinkInfo(
            resource_url=optional_text(info.get("resourceURL")),
            spase_resource_id=resource_id if resource_id and resource_id.startswith(SPASE_ID_PREFIX) else None,
        ),
        variables=variables,
        not_stored=not_stored,
        provenance=[
            MetadataSource(
                kind=MetadataSourceKind.HAPI, url=server_url, retrieved_at=retrieved_at, last_modified=optional_text(info.get("modificationDate"))
            )
        ],
        updated_at=retrieved_at,
    )


def hapi_parameter_variables(parameter: dict[str, Any]) -> list[VariableMetadata]:
    """Describes the stored fields of one HAPI parameter.

    A scalar parameter yields one variable, an array parameter one per element, named
    like the columns of the HAPI ingestion. Units, labels and vector components given as
    arrays apply per element; given as single value they apply to every element.

    Args:
        parameter: The definition of one parameter from the /info response.

    Returns:
        One variable per stored field of the parameter.
    """
    size = parameter.get("size")
    element_shape = tuple(size) if size else ()
    return [_column_variable(parameter, column, element_shape) for column in parameter_columns(parameter)]


def _column_variable(parameter: dict[str, Any], column: ParameterColumn, element_shape: tuple[int, ...]) -> VariableMetadata:
    """Describes the stored field of one column of a HAPI parameter.

    Args:
        parameter: The definition of the parameter from the /info response.
        column: The column of the parameter.
        element_shape: The HAPI 'size' of the parameter, empty for a scalar parameter.

    Returns:
        The variable, with every set field attributed to the source kind "hapi".
    """
    return VariableMetadata.from_single_source(
        MetadataSourceKind.HAPI,
        name=column.name,
        source_name=str(parameter["name"]),
        component=_component(column, element_shape),
        element_index=_flat_index(column, element_shape),
        description=optional_text(parameter.get("description")),
        label=column.label or optional_text(element_value(parameter.get("label"), column.element_index, element_shape)),
        dtype=HAPI_DATA_TYPES.get(str(parameter.get("type"))),
        units=normalize_units(element_value(parameter.get("units"), column.element_index, element_shape)),
        fill=_fill_value(parameter.get("fill")),
        coordinate_system=optional_text(parameter.get("coordinateSystemName")),
        vector_component=_vector_component(element_value(parameter.get("vectorComponents"), column.element_index, element_shape)),
    )


def element_value(value: Any, element_index: tuple[int, ...] | None, element_shape: tuple[int, ...]) -> Any:
    """Picks the value of a parameter attribute that applies to one array element.

    Args:
        value: The attribute value, a single value or an array shaped like the parameter.
        element_index: The index of the element in each dimension, None for a scalar parameter.
        element_shape: The HAPI 'size' of the parameter, empty for a scalar parameter.

    Returns:
        The single value itself, the array entry of the element, or None if the array does not match the parameter shape.
    """
    if not isinstance(value, list):
        return value
    if element_index is None:
        return None
    try:
        array_shape = np.shape(value)
    except ValueError:
        return None
    if array_shape != element_shape:
        return None
    entry: Any = value
    for index in element_index:
        entry = entry[index]
    return entry


def parse_hapi_time(value: Any) -> datetime | None:
    """Parses a HAPI time such as startDate or stopDate.

    HAPI times are UTC and may be a date with a trailing "Z", which datetime.fromisoformat rejects.

    Args:
        value: The time text as given by the server.

    Returns:
        The time in UTC, or None if it is missing, empty or not parsable.
    """
    text = optional_text(value)
    if text is None:
        return None
    text = text.strip()
    if "T" not in text and text.endswith("Z"):
        text = text[:-1]
    return parse_iso_time(text)


def hapi_timestamp_location(value: Any) -> TimestampLocation:
    """Normalizes the HAPI 'timeStampLocation' of a dataset.

    Args:
        value: The value as given by the server.

    Returns:
        The location in lower case, 'center' if absent, 'other' if it is outside the HAPI vocabulary.
    """
    text = optional_text(value)
    if text is None:
        return DEFAULT_TIMESTAMP_LOCATION
    location = text.strip().lower()
    if location in TIMESTAMP_LOCATIONS:
        return cast(TimestampLocation, location)
    return "other"


def doi_from_text(text: str | None) -> str | None:
    """Extracts a DOI from a text that consists of a DOI.

    Args:
        text: A bare DOI, a doi.org URL or a DOI with "doi:" prefix, or any other text.

    Returns:
        The bare DOI starting with "10.", or None if the text is no DOI.
    """
    if text is None:
        return None
    match = DOI_PATTERN.match(text.strip())
    return match.group(1) if match else None


def license_text(value: Any) -> str | None:
    """Converts the HAPI 'licenseURL' into one text.

    Args:
        value: One license URL or a list of license URLs.

    Returns:
        The URL, the URLs joined by ", ", or None if none is given.
    """
    if isinstance(value, list):
        urls = [url for url in (optional_text(item) for item in value) if url]
        return ", ".join(urls) if urls else None
    return optional_text(value)


def _flat_index(column: ParameterColumn, element_shape: tuple[int, ...]) -> int | None:
    """Computes the index of a column's element in the flattened array, where the last index varies fastest.

    Args:
        column: The column of the parameter.
        element_shape: The HAPI 'size' of the parameter, empty for a scalar parameter.

    Returns:
        The flat element index, or None for a scalar parameter.
    """
    if column.element_index is None:
        return None
    return int(np.ravel_multi_index(column.element_index, element_shape))


def _component(column: ParameterColumn, element_shape: tuple[int, ...]) -> str | int | None:
    """Names the array element a column holds.

    Args:
        column: The column of the parameter.
        element_shape: The HAPI 'size' of the parameter, empty for a scalar parameter.

    Returns:
        The label used in the column name, the element index for a vector, the flat index for
        an array with more than one dimension, or None for a scalar parameter.
    """
    if column.element_index is None:
        return None
    if column.label is not None:
        return column.label
    if len(column.element_index) == 1:
        return column.element_index[0]
    return _flat_index(column, element_shape)


def _fill_value(value: Any) -> str | int | float | None:
    """Converts the HAPI fill value into a stored value.

    Args:
        value: The fill value as given by the server, usually text.

    Returns:
        The value unchanged if it is text or a number, its text otherwise, None if missing or "null".
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return optional_text(value)


def _vector_component(value: Any) -> VectorComponent | None:
    """Checks a vector component against the HAPI 3.1 vocabulary.

    Args:
        value: The vector component as given by the server.

    Returns:
        The component, or None if it is missing or outside the vocabulary.
    """
    text = optional_text(value)
    if text is None or text.strip() not in VECTOR_COMPONENTS:
        return None
    return cast(VectorComponent, text.strip())
