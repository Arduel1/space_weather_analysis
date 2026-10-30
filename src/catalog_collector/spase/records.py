from typing import Any

import requests

from space_weather_analysis.clients.json_download import download_json
from space_weather_analysis.metadata.normalize import normalize_units, optional_text
from space_weather_analysis.util.error_handling import DataProcessingError, ResourceNotFoundError

# SPASE (Space Physics Archive Search and Extract, https://spase-group.org/) describes heliophysics datasets
# in records named by a resource ID `spase://{authority}/{path}`; spase-metadata.org serves each record as JSON.
SPASE_ID_PREFIX = "spase://"
SPASE_RECORD_URL = "https://spase-metadata.org/{resource_path}.json"

# Connect and read timeout in seconds; a record has some ten KB.
SPASE_TIMEOUT = (10.0, 60.0)


def spase_record_url(resource_id: str | None) -> str | None:
    """Builds the location of the JSON form of a SPASE record.

    Args:
        resource_id: The SPASE resource ID.

    Returns:
        The URL, or None if the ID is missing or not of the form `spase://{authority}/{path}`.
    """
    text = optional_text(resource_id)
    if text is None or not text.startswith(SPASE_ID_PREFIX):
        return None
    resource_path = text[len(SPASE_ID_PREFIX) :].strip("/")
    if "/" not in resource_path:
        return None
    return SPASE_RECORD_URL.format(resource_path=resource_path)


def fetch_spase_record(session: requests.Session, url: str) -> dict[str, Any] | None:
    """Downloads and parses one SPASE record.

    Args:
        session: The HTTP session for the request.
        url: The location of the record in JSON form.

    Returns:
        The record as returned by parse_spase_record, or None if no record exists at the location.

    Raises:
        DataRetrievalError: If the download fails; carries the Retry-After wait of HTTP 429 and 503.
        DataProcessingError: If the response is no SPASE record of numerical data.
    """
    try:
        document = download_json(session, url, SPASE_TIMEOUT, "SPASE record")
    except ResourceNotFoundError:
        return None
    try:
        return parse_spase_record(document)
    except (KeyError, TypeError, AttributeError) as e:
        raise DataProcessingError(f"SPASE record {url} is no record of numerical data: {e}") from e


def parse_spase_record(document: Any) -> dict[str, Any]:
    """Reduces a SPASE record of numerical data to the fields the metadata overlay reads.

    SPASE writes a repeated element as list and a single one as object, and numbers as text;
    both are normalized here.

    Args:
        document: The parsed JSON form of the record.

    Returns:
        The cadence as ISO 8601 duration and the parameters keyed by ParameterKey, the name of the
        variable in the data files, each with description, units, valid range, fill value,
        coordinate system and its elements with 0-based index.

    Raises:
        KeyError, TypeError, AttributeError: If the document is no SPASE record of numerical data.
    """
    numerical_data = document["Spase"]["NumericalData"]
    temporal_description = numerical_data.get("TemporalDescription") or {}
    parameters: dict[str, Any] = {}
    for parameter in _as_list(numerical_data.get("Parameter")):
        parameter_key = optional_text(parameter.get("ParameterKey"))
        if parameter_key is None:
            continue
        coordinate_system = parameter.get("CoordinateSystem") or {}
        structure = parameter.get("Structure") or {}
        parameters[parameter_key] = {
            "description": optional_text(parameter.get("Description")),
            "units": normalize_units(parameter.get("Units")),
            "valid_min": _number(parameter.get("ValidMin")),
            "valid_max": _number(parameter.get("ValidMax")),
            "fill": _number(parameter.get("FillValue")),
            "coordinate_system": optional_text(coordinate_system.get("CoordinateSystemName")),
            "coordinate_representation": optional_text(coordinate_system.get("CoordinateRepresentation")),
            "elements": [_element(element) for element in _as_list(structure.get("Element"))],
        }
    return {"cadence": optional_text(temporal_description.get("Cadence")), "parameters": parameters}


def _element(element: dict[str, Any]) -> dict[str, Any]:
    """Reduces one element of a SPASE parameter structure.

    Args:
        element: The element as given in the record, with its 1-based Index.

    Returns:
        Index (0-based, None if missing), name, qualifiers, units, valid range and fill value of the element.
    """
    index = _number(element.get("Index"))
    return {
        "index": int(index) - 1 if index is not None else None,
        "name": optional_text(element.get("Name")),
        "qualifiers": [text for text in (optional_text(qualifier) for qualifier in _as_list(element.get("Qualifier"))) if text],
        "units": normalize_units(element.get("Units")),
        "valid_min": _number(element.get("ValidMin")),
        "valid_max": _number(element.get("ValidMax")),
        "fill": _number(element.get("FillValue")),
    }


def _as_list(value: Any) -> list[Any]:
    """Returns a repeated SPASE element as list, whether the record gives a list, one object or nothing.

    Args:
        value: The element value.

    Returns:
        The list of entries; empty if the value is missing.
    """
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _number(value: Any) -> float | None:
    """Reads a SPASE number given as text.

    Args:
        value: The value as given in the record.

    Returns:
        The number, or None if the value is missing or no number, such as a time given as valid range.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = optional_text(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None
