import pathlib
from datetime import UTC, date, datetime

import pydantic
import pytest

from space_weather_analysis.metadata.curated import CuratedDataset, CuratedLink, CuratedReference, CuratedVariable, load_curated
from space_weather_analysis.metadata.models import DatasetMetadata, LinkInfo, MetadataSource, MetadataSourceKind, SourceInfo, VariableMetadata
from space_weather_analysis.metadata.overlays.curated import apply_curated_overlay
from space_weather_analysis.metadata.overlays.linked import apply_linked_overlay

PROJECT_CURATED_DIR = pathlib.Path(__file__).parents[3] / "config" / "metadata"
RETRIEVED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

# A valid reference table of a dataset `{dataset}`, appended to the curated TOML of a test
REFERENCE_TOML = """
[[dataset.{dataset}.reference]]
url = "https://provider.invalid/page"
retrieved = 2026-10-01
covers = "title"
"""


def _write_toml(directory: pathlib.Path, file_name: str, content: str) -> None:
    """Writes the content of a curated metadata file into a directory."""
    (directory / file_name).write_text(content, encoding="utf-8")


def test_project_curated_files_load_with_a_reference_per_dataset() -> None:
    """The curated files of the project load and every dataset names at least one reference."""
    curated = load_curated(PROJECT_CURATED_DIR)

    datasets_without_reference = [dataset_id for dataset_id, dataset in curated.items() if not dataset.reference]
    assert (bool(curated), datasets_without_reference) == (True, [])


def test_load_curated_merges_every_file_of_the_directory(tmp_path: pathlib.Path) -> None:
    """The datasets of all TOML files of a directory end up in one mapping by dataset ID."""
    _write_toml(tmp_path, "first.toml", '[dataset.alpha]\ntitle = "Alpha"\n' + REFERENCE_TOML.format(dataset="alpha"))
    _write_toml(tmp_path, "second.toml", '[dataset.beta]\ntitle = "Beta"\n' + REFERENCE_TOML.format(dataset="beta"))

    curated = load_curated(tmp_path)

    assert {dataset_id: dataset.title for dataset_id, dataset in curated.items()} == {"alpha": "Alpha", "beta": "Beta"}


@pytest.mark.parametrize(
    ("files", "expected_error"),
    [
        pytest.param({"one.toml": '[dataset.alpha]\ntitle = "Alpha"\n'}, pydantic.ValidationError, id="dataset-without-reference"),
        pytest.param(
            {"one.toml": '[dataset.alpha]\nheadline = "Alpha"\n' + REFERENCE_TOML.format(dataset="alpha")},
            pydantic.ValidationError,
            id="unknown-key",
        ),
        pytest.param(
            {
                "one.toml": '[[dataset.alpha.variable]]\nname = "flux"\n[[dataset.alpha.variable]]\nname = "flux"\n'
                + REFERENCE_TOML.format(dataset="alpha")
            },
            pydantic.ValidationError,
            id="variable-described-twice",
        ),
        pytest.param(
            {"one.toml": REFERENCE_TOML.format(dataset="alpha"), "two.toml": REFERENCE_TOML.format(dataset="alpha")},
            ValueError,
            id="dataset-in-two-files",
        ),
        pytest.param(
            {"one.toml": '[[dataset.alpha.reference]]\nurl = "https://provider.invalid/page"\nretrieved = "yesterday"\ncovers = "title"\n'},
            pydantic.ValidationError,
            id="retrieved-no-date",
        ),
    ],
)
def test_invalid_curated_data_is_rejected(tmp_path: pathlib.Path, files: dict[str, str], expected_error: type[Exception]) -> None:
    """Loading curated data that breaks a rule of the curated models raises the expected error."""
    for file_name, content in files.items():
        _write_toml(tmp_path, file_name, content)

    with pytest.raises(expected_error):
        load_curated(tmp_path)


def test_curated_overlay_wins_over_record_values_and_lists_one_provenance_entry_per_reference() -> None:
    """Curated values replace the record values and mark the variable fields as curated, add no variable and list the pages with what they cover."""
    record = DatasetMetadata(
        dataset_id="alpha",
        collection="alpha",
        source=SourceInfo(type="SWPC"),
        title="Provider title",
        variables=[VariableMetadata.from_single_source(MetadataSourceKind.FILE, name="flux", source_name="flux", units="pfu")],
        provenance=[
            MetadataSource(kind=MetadataSourceKind.FILE, url="https://provider.invalid/data.json", retrieved_at=RETRIEVED_AT),
            MetadataSource(kind=MetadataSourceKind.CURATED, url="https://provider.invalid/old", retrieved_at=RETRIEVED_AT),
        ],
    )
    curated = CuratedDataset(
        title="Curated title",
        variable=[CuratedVariable(name="flux", units="W/m^2", valid_min=0), CuratedVariable(name="missing", units="nT")],
        reference=[
            CuratedReference(url="https://provider.invalid/page", retrieved=date(2026, 10, 2), covers="title"),
            CuratedReference(url="https://provider.invalid/units", retrieved=date(2026, 10, 3), covers="units"),
        ],
    )

    apply_curated_overlay(record, curated)

    assert (record.title, [variable.model_dump() for variable in record.variables], record.provenance) == (
        "Curated title",
        [
            VariableMetadata(
                name="flux",
                source_name="flux",
                units="W/m^2",
                valid_min=0,
                field_sources={
                    "name": MetadataSourceKind.FILE,
                    "source_name": MetadataSourceKind.FILE,
                    "units": MetadataSourceKind.CURATED,
                    "valid_min": MetadataSourceKind.CURATED,
                },
            ).model_dump()
        ],
        [
            MetadataSource(kind=MetadataSourceKind.FILE, url="https://provider.invalid/data.json", retrieved_at=RETRIEVED_AT),
            MetadataSource(
                kind=MetadataSourceKind.CURATED, url="https://provider.invalid/page", retrieved_at=datetime(2026, 10, 2, tzinfo=UTC), covers="title"
            ),
            MetadataSource(
                kind=MetadataSourceKind.CURATED, url="https://provider.invalid/units", retrieved_at=datetime(2026, 10, 3, tzinfo=UTC), covers="units"
            ),
        ],
    )


def test_linked_overlay_fills_only_missing_values_and_marks_them_linked() -> None:
    """A linked field takes the missing description and valid range, keeps its unit and gets no fill; the record gets SPASE ID and provenance."""
    linked_record = DatasetMetadata(
        dataset_id="RICH",
        collection="RICH",
        source=SourceInfo(type="HAPI"),
        links=LinkInfo(resource_url="https://provider.invalid/rich", spase_resource_id="spase://NASA/NumericalData/RICH"),
        variables=[
            VariableMetadata.from_single_source(
                MetadataSourceKind.HAPI, name="V", source_name="V", description="Flow speed", units="Km/s", fill="9999.0", valid_min=0
            )
        ],
        updated_at=RETRIEVED_AT,
    )
    record = DatasetMetadata(
        dataset_id="poor",
        collection="poor",
        source=SourceInfo(type="SWPC"),
        variables=[
            VariableMetadata.from_single_source(MetadataSourceKind.CURATED, name="speed", source_name="speed", units="km/s"),
            VariableMetadata(name="other", source_name="other"),
        ],
    )

    apply_linked_overlay(record, linked_record, CuratedLink(dataset="RICH", fields={"speed": "V"}))

    speed, other = record.variables
    assert (speed.description, speed.units, speed.fill, speed.valid_min, speed.field_sources, other.description) == (
        "Flow speed",
        "km/s",
        None,
        0.0,
        {
            "name": MetadataSourceKind.CURATED,
            "source_name": MetadataSourceKind.CURATED,
            "units": MetadataSourceKind.CURATED,
            "description": MetadataSourceKind.LINKED,
            "valid_min": MetadataSourceKind.LINKED,
        },
        None,
    )
    assert (record.links.spase_resource_id, record.links.documentation, [(entry.kind, entry.url) for entry in record.provenance]) == (
        "spase://NASA/NumericalData/RICH",
        ["https://provider.invalid/rich"],
        [(MetadataSourceKind.LINKED, "https://provider.invalid/rich")],
    )
