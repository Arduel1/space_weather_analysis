from datetime import UTC, datetime

from space_weather_analysis.metadata.gfz import build_gfz_metadata
from space_weather_analysis.metadata.models import (
    CitationInfo,
    DatasetMetadata,
    DataType,
    MetadataSource,
    MetadataSourceKind,
    SourceInfo,
    TimeInfo,
    TimestampLocation,
    VariableMetadata,
)

SERVER_URL = "https://gfz.example.org/api"
RETRIEVED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
CURATED = MetadataSourceKind.CURATED
KP_VARIABLE = VariableMetadata.from_single_source(
    CURATED,
    name="Kp",
    source_name="Kp",
    description="Planetary geomagnetic Kp index, 0 to 9 in steps of one third",
    dtype=DataType.DOUBLE,
)


def _expected_record(variables: list[VariableMetadata], provider: str | None, license_text: str | None) -> DatasetMetadata:
    """Builds the record expected for the Kp index with the given variables and response meta values."""
    return DatasetMetadata(
        dataset_id="Kp",
        collection="kp",
        source=SourceInfo(type="GFZ_POTSDAM", provider=provider, server_url=SERVER_URL),
        time=TimeInfo(cadence="PT3H", timestamp_location=TimestampLocation.BEGIN),
        citation=CitationInfo(license=license_text),
        variables=variables,
        provenance=[
            MetadataSource(kind=MetadataSourceKind.API, url=SERVER_URL, retrieved_at=RETRIEVED_AT),
            MetadataSource(kind=CURATED, retrieved_at=RETRIEVED_AT),
        ],
        updated_at=RETRIEVED_AT,
    )


def test_record_with_status_carries_provider_license_and_status_meanings() -> None:
    """Provider and license come from the response meta; the status variable lists the meaning of each status."""
    meta = {"source": "GFZ German Research Centre for Geosciences", "license": "CC BY 4.0"}

    record = build_gfz_metadata("Kp", "kp", meta, True, SERVER_URL, RETRIEVED_AT)

    status_variable = VariableMetadata.from_single_source(
        CURATED,
        name="status",
        source_name="status",
        description="Status of the index value",
        dtype=DataType.STRING,
        flag_meanings={"def": "definitive", "pre": "preliminary"},
    )
    assert record == _expected_record([KP_VARIABLE, status_variable], "GFZ German Research Centre for Geosciences", "CC BY 4.0")


def test_record_without_status_and_empty_meta_has_only_the_index_variable() -> None:
    """Without status field only the index is described; a meta without source and license leaves both unset."""
    record = build_gfz_metadata("Kp", "kp", {}, False, SERVER_URL, RETRIEVED_AT)

    assert record == _expected_record([KP_VARIABLE], None, None)
