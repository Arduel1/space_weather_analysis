import json
import pathlib
from datetime import UTC, datetime

from space_weather_analysis.metadata.spase.dictionary import load_spase_dictionary, lookup_definition

# An excerpt of the dictionary of the SPASE data model in its JSON form
SPASE_MODEL = {
    "name": "SPASE",
    "dictionary": {
        "Heliosphere": {"term": "Heliosphere", "type": "Enumeration", "definition": "The solar atmosphere."},
        "NearEarth": {"term": "NearEarth", "type": "Item", "definition": "The heliospheric region near the Earth."},
        "ThermalPlasma": {"term": "ThermalPlasma", "type": "Item", "definition": "Measurements of the plasma."},
    },
}


def test_definitions_are_read_from_a_fresh_cache_file_without_download(tmp_path: pathlib.Path) -> None:
    """A cache file younger than a day is read without request, and a dotted term gets the definition of its most specific part."""
    cache_path = tmp_path / "spase_dictionary.json"
    cache_path.write_text(json.dumps(SPASE_MODEL), encoding="utf-8")

    definitions = load_spase_dictionary(cache_path, now=datetime.now(UTC))

    assert [lookup_definition(definitions, term) for term in ["Heliosphere.NearEarth", "ThermalPlasma", "CoronalMassEjection"]] == [
        "The heliospheric region near the Earth.",
        "Measurements of the plasma.",
        None,
    ]
