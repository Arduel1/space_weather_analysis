import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.metadata.hapi import doi_from_text, parse_hapi_time
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSource
from space_weather_analysis.metadata.normalize import optional_text
from space_weather_analysis.metadata.repository import MetadataRepository

# Host of the CDAWeb HAPI server, whose datasets CDAS also describes.
CDAWEB_HOST = "cdaweb.gsfc.nasa.gov"

# The CDAWeb HAPI server splits a dataset with several time variables (ISTP DEPEND_0) into one
# HAPI dataset per time variable, named `{dataset_id}@{index}`; CDAS lists only `{dataset_id}`.
HAPI_SPLIT_SUFFIX = re.compile(r"@\d+$")


def cdas_dataset_id(dataset_id: str) -> str:
    """Returns the dataset ID under which CDAS lists a CDAWeb dataset.

    Args:
        dataset_id: The dataset ID in the master catalog.

    Returns:
        The ID without the HAPI split suffix `@{index}`.
    """
    return HAPI_SPLIT_SUFFIX.sub("", dataset_id)


def describes_cdaweb_dataset(record: DatasetMetadata) -> bool:
    """Checks whether a metadata record describes a dataset that CDAWeb serves.

    Args:
        record: The metadata record.

    Returns:
        True for records ingested through SunPy Fido from CDAWeb or from the CDAWeb HAPI server.
    """
    if record.source.type == SourceType.FIDO_CDAWEB:
        return True
    server_host = urlsplit(record.source.server_url or "").hostname or ""
    return record.source.type == SourceType.HAPI and server_host.lower() == CDAWEB_HOST


def cdas_time_interval(payload: Dict[str, Any]) -> Optional[Tuple[str, str, datetime, datetime]]:
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
    return start_text, stop_text, start_time, stop_time


def apply_cdas_overlay(record: DatasetMetadata, payload: Dict[str, Any], retrieved_at: datetime, url: Optional[str]) -> None:
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
        record.time.coverage_start, record.time.coverage_stop = time_interval[2], time_interval[3]

    provenance = [source for source in record.provenance if source.kind != "cdas"]
    provenance.append(MetadataSource(kind="cdas", url=url, retrieved_at=retrieved_at))
    record.provenance = provenance


def save_with_overlays(metadata_repository: MetadataRepository, records: Sequence[DatasetMetadata]) -> None:
    """Saves records after laying the stored metadata of further sources over them.

    Every writer of normalized records saves through this function, so a record rebuilt from one
    source keeps the fields another source is authoritative for. The overlay comes from the raw
    metadata of kind "cdas" stored for CDAWeb datasets.

    Args:
        metadata_repository: The repository holding records and raw metadata.
        records: The records to save, changed in place by the overlays.
    """
    cdaweb_records: List[DatasetMetadata] = [record for record in records if describes_cdaweb_dataset(record)]
    if cdaweb_records:
        cdas_ids = sorted({cdas_dataset_id(record.dataset_id) for record in cdaweb_records})
        raw_cdas_records = metadata_repository.get_raw_many(cdas_ids, "cdas")
        for record in cdaweb_records:
            raw_cdas_record = raw_cdas_records.get(cdas_dataset_id(record.dataset_id))
            if raw_cdas_record is not None:
                apply_cdas_overlay(record, raw_cdas_record.payload, raw_cdas_record.retrieved_at, raw_cdas_record.url)
    metadata_repository.save_many(records)
