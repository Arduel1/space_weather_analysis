import json
import logging
import pathlib
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any

import requests
from pydantic_settings import BaseSettings, SettingsConfigDict

from space_weather_analysis.clients.download import download_json
from space_weather_analysis.util.atomic_files import write_text_atomic
from space_weather_analysis.util.error_handling import SpaceWeatherError

logger = logging.getLogger(__name__)

# The SPASE data model (Space Physics Archive Search and Extract, https://spase-group.org/) defines every term a SPASE
# record uses, such as the observed region Heliosphere.NearEarth, in the dictionary of its JSON form.
SPASE_DICTIONARY_URL = "https://spase-group.org/data/model/spase-latest.json"

# Connect and read timeout in seconds; the model has some MB.
SPASE_DICTIONARY_TIMEOUT = (10.0, 60.0)

# A cached dictionary older than this is downloaded again.
SPASE_DICTIONARY_MAX_AGE = timedelta(days=1)


class SpaseDictionarySettings(BaseSettings):
    """Settings of the SPASE dictionary, read from environment variables `SPASE_DICTIONARY_{field name}` in upper case.

    Attributes:
        cache_path: The file holding the downloaded SPASE data model.
    """

    model_config = SettingsConfigDict(env_prefix="SPASE_DICTIONARY_", frozen=True)

    cache_path: pathlib.Path = pathlib.Path("data/spase_dictionary.json")


def parse_spase_dictionary(document: Any) -> dict[str, str]:
    """Reads the term definitions of the SPASE data model.

    Args:
        document: The parsed JSON form of the data model, whose `dictionary` holds one entry with `definition` per term.

    Returns:
        The definition by term; empty if the document holds no dictionary.
    """
    entries = document.get("dictionary") if isinstance(document, dict) else None
    if not isinstance(entries, dict):
        return {}
    definitions: dict[str, str] = {}
    for term, entry in entries.items():
        definition = entry.get("definition") if isinstance(entry, dict) else None
        if isinstance(definition, str) and definition.strip():
            definitions[term] = definition.strip()
    return definitions


def load_spase_dictionary(cache_path: pathlib.Path, now: datetime | None = None) -> dict[str, str]:
    """Reads the term definitions from the cache file.

    The data model is downloaded first if the file is missing or outdated. A failed download keeps an outdated file in
    use; without network and file there are no definitions.

    Args:
        cache_path: The file holding the downloaded data model.
        now: The current time; the system clock if None.

    Returns:
        The definition by term; empty if no dictionary is available.
    """
    if not _is_fresh(cache_path, now or datetime.now(UTC)):
        _download_into(cache_path)
    try:
        with cache_path.open(encoding="utf-8") as file:
            return parse_spase_dictionary(json.load(file))
    except (OSError, ValueError) as e:
        logger.warning("No SPASE dictionary available at %s: %s", cache_path, e)
        return {}


def lookup_definition(definitions: dict[str, str], term: str) -> str | None:
    """Finds the definition of a SPASE term.

    The dictionary defines each part of a dotted term on its own, so a dotted term gets the definition of its most
    specific defined part: NearEarth for Heliosphere.NearEarth.

    Args:
        definitions: The definition by term.
        term: The term as written in a SPASE record.

    Returns:
        The definition, or None if the dictionary defines no part of the term.
    """
    if term in definitions:
        return definitions[term]
    # A term and its subdivisions are joined by dots; the most specific part with a definition explains the term
    for part in reversed(term.split(".")):
        if part in definitions:
            return definitions[part]
    return None


def spase_definition(term: str) -> str | None:
    """Finds the definition of a SPASE term in the dictionary of the configured cache file.

    The file is read once per process and day.

    Args:
        term: The term as written in a SPASE record.

    Returns:
        The definition, or None if the term or the whole dictionary is not available.
    """
    definitions = _definitions_of_day(SpaseDictionarySettings().cache_path, datetime.now(UTC).date())
    return lookup_definition(definitions, term)


@lru_cache(maxsize=1)
def _definitions_of_day(cache_path: pathlib.Path, day: date) -> dict[str, str]:  # noqa: ARG001
    """Reads the definitions once per cache file and day; the day is only part of the cache key."""
    return load_spase_dictionary(cache_path)


def _is_fresh(cache_path: pathlib.Path, now: datetime) -> bool:
    """Checks whether the cache file exists and is younger than SPASE_DICTIONARY_MAX_AGE."""
    try:
        modified_at = datetime.fromtimestamp(cache_path.stat().st_mtime, UTC)
    except OSError:
        return False
    return now - modified_at < SPASE_DICTIONARY_MAX_AGE


def _download_into(cache_path: pathlib.Path) -> None:
    """Downloads the data model and replaces the cache file with it if it holds a dictionary.

    A failure is logged and leaves the file unchanged, as an outdated dictionary stays usable.
    """
    try:
        with requests.Session() as session:
            document = download_json(session, SPASE_DICTIONARY_URL, SPASE_DICTIONARY_TIMEOUT, "SPASE dictionary")
        if not parse_spase_dictionary(document):
            logger.warning("SPASE data model %s holds no dictionary; the cache file is kept.", SPASE_DICTIONARY_URL)
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(cache_path, json.dumps(document))
    except (SpaceWeatherError, OSError) as e:
        logger.warning("SPASE dictionary not downloaded into %s: %s", cache_path, e)
