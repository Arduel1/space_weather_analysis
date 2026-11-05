from collections.abc import Iterator
from pathlib import Path

import pytest

from space_weather_analysis.metadata.curated import configured_curated


@pytest.fixture(autouse=True)
def no_curated_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Points the curated metadata to an empty directory, so records hold only what a test builds; tests may write files there."""
    directory = tmp_path_factory.mktemp("curated")
    monkeypatch.setenv("METADATA_CURATED_DIR", str(directory))
    configured_curated.cache_clear()
    yield directory
    configured_curated.cache_clear()
