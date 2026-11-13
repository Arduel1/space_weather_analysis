import json
import pathlib
from datetime import UTC, datetime
from typing import Any

import pytest

from space_weather_analysis.metadata.spase import resources as spase_resources
from space_weather_analysis.metadata.spase.resources import SpaseResource, parse_spase_resource, spase_resource
from space_weather_analysis.util.error_handling import DataRetrievalError


def test_a_description_of_only_white_space_counts_as_none() -> None:
    """A record whose description is blank yields no description instead of an empty text."""
    record = {"Spase": {"NumericalData": {"ResourceID": "spase://Test/Data", "ResourceHeader": {"Description": "  \n "}}}}

    assert parse_spase_resource(record) == SpaseResource()


def test_a_cache_entry_with_an_unreadable_time_stays_in_use_when_the_download_fails(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An entry whose download time cannot be read is treated as outdated, not as an error, and kept without network."""
    cache_path = tmp_path / "spase_resources.json"
    entry = {"description": "Cached text", "observed_regions": ["Heliosphere"], "measurement_types": [], "retrieved_at": "not a time"}
    cache_path.write_text(json.dumps({"spase://Test/Data": entry}), encoding="utf-8")
    monkeypatch.setenv("SPASE_RESOURCE_CACHE_PATH", str(cache_path))

    def unavailable(*_: Any) -> Any:
        raise DataRetrievalError("no network")

    monkeypatch.setattr(spase_resources, "download_json", unavailable)

    resource = spase_resource("spase://Test/Data", now=datetime(2026, 11, 10, tzinfo=UTC))

    assert resource == SpaseResource(description="Cached text", observed_regions=["Heliosphere"], measurement_types=[])
