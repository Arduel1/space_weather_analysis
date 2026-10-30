from typing import Any

import requests

from space_weather_analysis.clients.json_download import download_json
from space_weather_analysis.util.error_handling import DataProcessingError

# Connect and read timeout in seconds; the largest master files have a few MB.
MASTER_TIMEOUT = (10.0, 120.0)


def fetch_master(session: requests.Session, url: str) -> dict[str, Any]:
    """Downloads and parses the master file of one CDAWeb dataset.

    CDAWeb publishes per dataset a master file (https://cdaweb.gsfc.nasa.gov/pub/software/cdawlib/0JSONS/) holding
    the ISTP attributes of every variable (International Solar-Terrestrial Physics Metadata Guidelines,
    https://istp-metadata.readthedocs.io/) and the data of the variables that do not vary in time (VarData).

    Args:
        session: The HTTP session for the request.
        url: The location of the master file in JSON format.

    Returns:
        The master file as returned by parse_master.

    Raises:
        DataRetrievalError: If the download fails; carries the Retry-After wait of HTTP 429 and 503.
        DataProcessingError: If the response is no master file.
    """
    document = download_json(session, url, MASTER_TIMEOUT, "CDAWeb master file")
    try:
        return parse_master(document)
    except ValueError as e:
        raise DataProcessingError(f"CDAWeb master file {url} is malformed: {e}") from e


def parse_master(document: Any) -> dict[str, Any]:
    """Converts a master file into plain mappings.

    The file holds one entry named after the master CDF file, whose global attributes and variables are lists of
    single-key mappings; each multi-entry attribute is a list of `{index: value}` mappings.

    Args:
        document: The parsed JSON of the master file.

    Returns:
        `globals` mapping each global attribute to its value, or to the list of its values if it has several
        entries, and `variables` mapping each variable name to its `description` (VarDescription), its
        `attributes` (VarAttributes) and, if present, its `data` (VarData).

    Raises:
        ValueError: If the document does not have the structure of a master file.
    """
    if not isinstance(document, dict) or len(document) != 1:
        raise ValueError("expected one entry named after the master CDF file")
    master = next(iter(document.values()))
    if not isinstance(master, dict) or not isinstance(master.get("CDFVariables"), list):
        raise ValueError("expected CDFVariables")

    global_attributes: dict[str, Any] = {}
    for name, entries in _single_key_items(master.get("CDFglobalAttributes") or []):
        values = [value for _, value in _single_key_items(entries)] if isinstance(entries, list) else [entries]
        global_attributes[name] = values[0] if len(values) == 1 else values

    variables: dict[str, Any] = {}
    for name, parts in _single_key_items(master["CDFVariables"]):
        variables[name] = _parse_variable(parts if isinstance(parts, list) else [])
    return {"globals": global_attributes, "variables": variables}


def _parse_variable(parts: list[Any]) -> dict[str, Any]:
    """Converts the parts of one master variable into plain mappings.

    Args:
        parts: The list of `VarDescription`, `VarAttributes` and optional `VarData` entries.

    Returns:
        The `description` and `attributes` as mappings and, if present, the `data`.
    """
    variable: dict[str, Any] = {"description": {}, "attributes": {}}
    for part_name, content in _single_key_items(parts):
        if part_name == "VarDescription":
            variable["description"] = dict(_single_key_items(content if isinstance(content, list) else []))
        elif part_name == "VarAttributes":
            variable["attributes"] = dict(_single_key_items(content if isinstance(content, list) else []))
        elif part_name == "VarData":
            variable["data"] = content
    return variable


def _single_key_items(entries: list[Any]) -> list[tuple[str, Any]]:
    """Unpacks a master file list of single-key mappings into name and value pairs.

    Args:
        entries: The list as given in the master file.

    Returns:
        The name and value of each mapping in list order; entries that are no mapping are left out.
    """
    items: list[tuple[str, Any]] = []
    for entry in entries:
        if isinstance(entry, dict):
            items.extend((str(name), value) for name, value in entry.items())
    return items
