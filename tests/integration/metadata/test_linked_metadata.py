import pathlib
from datetime import UTC, datetime

import pytest
from pymongo.database import Database

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.metadata.minimal import build_minimal_metadata
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSourceKind, SourceInfo, VariableMetadata
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MongoMetadataRepository

RETRIEVED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

# Curated metadata linking the speed of a dataset without provider metadata to the variable V of the dataset RICH
LINKED_CURATED_TOML = """
[dataset.poor]
title = "Poor dataset"
same_as = { dataset = "RICH", fields = { speed = "V" } }

[[dataset.poor.reference]]
url = "https://provider.invalid/page"
retrieved = 2026-10-01
covers = "title"
"""


def _poor_record() -> DatasetMetadata:
    """Builds the record of the dataset without provider metadata as its pipeline does."""
    return build_minimal_metadata("poor", "poor", SourceType.SWPC, ["speed"], "https://provider.invalid/data.json", RETRIEVED_AT)


@pytest.mark.integration
def test_linked_values_are_copied_on_the_first_save_after_the_linked_record_exists(db: Database, no_curated_metadata: pathlib.Path) -> None:
    """Without a stored linked record a save copies nothing; once the linked record is stored, the next save copies its description."""
    (no_curated_metadata / "linked.toml").write_text(LINKED_CURATED_TOML, encoding="utf-8")
    repository = MongoMetadataRepository(db)

    save_with_overlays(repository, [_poor_record()])
    before = repository.get("poor")
    repository.save(
        DatasetMetadata(
            dataset_id="RICH",
            collection="RICH",
            source=SourceInfo(type=SourceType.HAPI),
            variables=[VariableMetadata.from_single_source(MetadataSourceKind.HAPI, name="V", source_name="V", description="Flow speed")],
        )
    )
    save_with_overlays(repository, [_poor_record()])
    after = repository.get("poor")

    assert before is not None and after is not None
    assert (before.title, before.variables[0].description, [entry.kind for entry in before.provenance]) == (
        "Poor dataset",
        None,
        [MetadataSourceKind.FILE, MetadataSourceKind.CURATED],
    )
    assert (after.variables[0].description, [entry.kind for entry in after.provenance]) == (
        "Flow speed",
        [MetadataSourceKind.FILE, MetadataSourceKind.LINKED, MetadataSourceKind.CURATED],
    )
