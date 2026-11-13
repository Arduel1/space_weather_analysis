import json
import logging
import pathlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from pydantic_settings import BaseSettings, SettingsConfigDict

from space_weather_analysis.clients.download import download_json
from space_weather_analysis.util.atomic_files import write_text_atomic
from space_weather_analysis.util.error_handling import SpaceWeatherError

logger = logging.getLogger(__name__)

# The JSON form of a SPASE record (Space Physics Archive Search and Extract, https://spase-group.org/) by the path of
# its resource ID `spase://{path}`
SPASE_RECORD_URL = "https://spase-metadata.org/{path}.json"
# Scheme that starts every SPASE resource ID
SPASE_ID_PREFIX = "spase://"
# Connect and read timeout in seconds
SPASE_RECORD_TIMEOUT = (10.0, 30.0)
# A cached record older than this is downloaded again; SPASE records change rarely
SPASE_RECORD_MAX_AGE = timedelta(days=7)


class SpaseResourceSettings(BaseSettings):
    """Settings of the SPASE record cache, read from environment variables `SPASE_RESOURCE_{field name}` in upper case.

    Attributes:
        cache_path: The file holding the terms read from downloaded SPASE records, by resource ID.
    """

    model_config = SettingsConfigDict(env_prefix="SPASE_RESOURCE_", frozen=True)

    cache_path: pathlib.Path = pathlib.Path("data/spase_resources.json")


@dataclass(frozen=True)
class SpaseResource:
    """What a SPASE record states about a data resource.

    Attributes:
        description: The description of its resource header; None if it gives none.
        observed_regions: The ObservedRegion terms.
        measurement_types: The MeasurementType terms.
    """

    description: str | None = None
    observed_regions: list[str] = field(default_factory=list)
    measurement_types: list[str] = field(default_factory=list)


def _terms(value: Any) -> list[str]:
    """Reads a SPASE element that holds one term or a list of terms, keeping the texts that are not empty."""
    values = value if isinstance(value, list) else [value]
    return [term for term in values if isinstance(term, str) and term]


def parse_spase_resource(document: Any) -> SpaseResource:
    """Reads description, observed regions and measurement types from the JSON form of a SPASE record.

    Args:
        document: The parsed record, whose `Spase` element holds one resource element such as NumericalData.

    Returns:
        The resource; empty values where the record gives none.
    """
    spase = document.get("Spase") if isinstance(document, dict) else None
    if not isinstance(spase, dict):
        return SpaseResource()
    resources = [value for value in spase.values() if isinstance(value, dict) and "ResourceID" in value]
    if not resources:
        return SpaseResource()
    resource = resources[0]
    header = resource.get("ResourceHeader")
    description = header.get("Description") if isinstance(header, dict) else None
    # A description of only white space counts as none
    stripped = description.strip() if isinstance(description, str) else ""
    return SpaseResource(
        description=stripped or None,
        observed_regions=_terms(resource.get("ObservedRegion")),
        measurement_types=_terms(resource.get("MeasurementType")),
    )


def _read_cache(cache_path: pathlib.Path) -> dict[str, Any]:
    """Reads the cache file; empty if it is missing or unreadable."""
    try:
        with cache_path.open(encoding="utf-8") as file:
            content = json.load(file)
    except (OSError, ValueError):
        return {}
    return content if isinstance(content, dict) else {}


def _write_cache(cache_path: pathlib.Path, content: dict[str, Any]) -> None:
    """Writes the cache file through a temporary file in its directory; a failure is logged, as the cache is optional."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(cache_path, json.dumps(content))
    except OSError as e:
        logger.warning("SPASE record cache %s not written: %s", cache_path, e)


def _retrieved_at(entry: object) -> datetime | None:
    """Returns the download time of a cache entry; None for a missing entry or a time that is unreadable or lacks a time zone."""
    if not isinstance(entry, dict) or not isinstance(entry.get("retrieved_at"), str):
        return None
    try:
        retrieved = datetime.fromisoformat(entry["retrieved_at"])
    except ValueError:
        return None
    return retrieved if retrieved.tzinfo is not None else None


def spase_resource(resource_id: str, now: datetime | None = None) -> SpaseResource | None:
    """Returns what the SPASE record of a resource ID states.

    The record is read from the cache, or downloaded when the cache lacks it or is outdated. A failed download keeps an
    outdated cache entry in use; without network and entry there is no resource.

    Args:
        resource_id: The SPASE resource ID, `spase://{path}`.
        now: The current time; the system clock if None.

    Returns:
        The resource; None if the ID is no SPASE resource ID or no record is available.
    """
    if not resource_id.startswith(SPASE_ID_PREFIX):
        return None
    cache_path = SpaseResourceSettings().cache_path
    cache = _read_cache(cache_path)
    entry = cache.get(resource_id)
    current = now or datetime.now(UTC)
    retrieved = _retrieved_at(entry)
    if retrieved is None or current - retrieved > SPASE_RECORD_MAX_AGE:
        url = SPASE_RECORD_URL.format(path=resource_id.removeprefix(SPASE_ID_PREFIX).strip("/"))
        try:
            with requests.Session() as session:
                resource = parse_spase_resource(download_json(session, url, SPASE_RECORD_TIMEOUT, "SPASE record"))
        except SpaceWeatherError as e:
            logger.warning("SPASE record %s not downloaded: %s", resource_id, e)
        else:
            entry = {**asdict(resource), "retrieved_at": current.isoformat()}
            cache[resource_id] = entry
            _write_cache(cache_path, cache)
    if not isinstance(entry, dict):
        return None
    return SpaseResource(
        description=entry.get("description"),
        observed_regions=list(entry.get("observed_regions") or []),
        measurement_types=list(entry.get("measurement_types") or []),
    )
