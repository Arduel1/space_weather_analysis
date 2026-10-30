from datetime import datetime
from typing import Any, NamedTuple

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.metadata.hapi import doi_from_text, parse_hapi_time
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSource
from space_weather_analysis.metadata.normalize import optional_text


class CdasTimeInterval(NamedTuple):
    """The time range of a CDAS dataset entry.

    Attributes:
        start: The start time text as given by CDAS.
        stop: The stop time text as given by CDAS.
        start_time: The parsed start time in UTC.
        stop_time: The parsed stop time in UTC.
    """

    start: str
    stop: str
    start_time: datetime
    stop_time: datetime


def cdas_time_interval(payload: dict[str, Any]) -> CdasTimeInterval | None:
    """Reads the time range of a CDAS dataset entry.

    CDAS writes ISO 8601 UTC times like HAPI, so the HAPI time parser reads them.

    Args:
        payload: The dataset entry as listed by CDAS.

    Returns:
        Start and stop as given and parsed in UTC, or None if either is missing or not parsable.
    """
    interval = payload.get("TimeInterval")
    if not isinstance(interval, dict):
        return None
    start_text, stop_text = optional_text(interval.get("Start")), optional_text(interval.get("End"))
    start_time, stop_time = parse_hapi_time(start_text), parse_hapi_time(stop_text)
    if start_text is None or stop_text is None or start_time is None or stop_time is None:
        return None
    return CdasTimeInterval(start_text, stop_text, start_time, stop_time)


def apply_cdas_overlay(record: DatasetMetadata, payload: dict[str, Any], retrieved_at: datetime, url: str | None) -> None:
    """Sets the dataset fields for which CDAS is the authoritative source.

    DOI, SPASE resource ID and the CDAWeb notes page replace the values of other sources when CDAS
    gives them. The coverage is set only for records ingested through SunPy Fido, as HAPI records
    take it from the HAPI server.

    Args:
        record: The metadata record, changed in place.
        payload: The dataset entry as listed by CDAS.
        retrieved_at: When the entry was read.
        url: The location the entry was read from.
    """
    doi = doi_from_text(optional_text(payload.get("Doi")))
    if doi:
        record.citation.doi = doi
    spase_resource_id = optional_text(payload.get("SpaseResourceId"))
    if spase_resource_id:
        record.links.spase_resource_id = spase_resource_id
    notes_url = optional_text(payload.get("Notes"))
    if notes_url:
        record.links.resource_url = notes_url

    time_interval = cdas_time_interval(payload)
    if record.source.type == SourceType.FIDO_CDAWEB and time_interval is not None:
        record.time.coverage_start, record.time.coverage_stop = time_interval.start_time, time_interval.stop_time

    record.replace_provenance(MetadataSource(kind="cdas", url=url, retrieved_at=retrieved_at))
