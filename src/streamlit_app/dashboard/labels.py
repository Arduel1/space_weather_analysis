from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

# A metadata record as JSON-compatible dictionary, as the read layer returns it; empty if the dataset has none
type Metadata = Mapping[str, Any]

NO_METADATA = "no metadata"


def _variable(metadata: Metadata, field: str) -> Mapping[str, Any]:
    """Returns the metadata of one variable of a record; empty if the record does not describe it."""
    for variable in metadata.get("variables", []):
        if variable.get("name") == field:
            return variable
    return {}


def dataset_title(metadata: Metadata, configured: str = "") -> str:
    """Names a dataset: the configured title, else the title of its metadata record, else nothing.

    Args:
        metadata: The metadata record of the dataset.
        configured: The title given in the configuration; empty for none.

    Returns:
        The title; empty if neither gives one.
    """
    return configured or metadata.get("title") or ""


def field_label(metadata: Metadata, field: str, configured: str = "") -> str:
    """Names a field: the configured label, else the label of its variable, else the field name.

    Args:
        metadata: The metadata record of the dataset.
        field: The data field.
        configured: The label given in the configuration; empty for none.

    Returns:
        The label.
    """
    return configured or _variable(metadata, field).get("label") or field


def field_unit(metadata: Metadata, field: str, configured: str = "") -> str:
    """Returns the unit of a field: the configured unit, else the unit of its variable, else nothing.

    Args:
        metadata: The metadata record of the dataset.
        field: The data field.
        configured: The unit given in the configuration; empty for none.

    Returns:
        The unit; empty if neither gives one.
    """
    return configured or _variable(metadata, field).get("units") or ""


def field_description(metadata: Metadata, field: str) -> str:
    """Returns the description of a field from its variable; empty if the record gives none."""
    return _variable(metadata, field).get("description") or ""


def attribution(metadata: Metadata) -> str:
    """Names the source of a dataset with its terms: provider, else server host, then license and DOI.

    Args:
        metadata: The metadata record of the dataset.

    Returns:
        The attribution; NO_METADATA for a dataset without record.
    """
    if not metadata:
        return NO_METADATA
    source = metadata.get("source", {})
    citation = metadata.get("citation", {})
    provider = source.get("provider") or urlsplit(source.get("server_url") or "").hostname or source.get("type") or "unknown provider"
    parts = [provider]
    if citation.get("license"):
        parts.append(citation["license"])
    if citation.get("doi"):
        parts.append(f"doi:{citation['doi']}")
    return ", ".join(parts)


def sources_line(records: Iterable[Metadata]) -> str:
    """Joins the attributions of the datasets of a panel into one line, each attribution once, in the given order.

    Args:
        records: The metadata records of the datasets.

    Returns:
        The line starting with "Source:".
    """
    attributions = list(dict.fromkeys(attribution(record) for record in records))
    return "Source: " + "; ".join(attributions)
