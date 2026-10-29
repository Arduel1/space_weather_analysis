from datetime import datetime, timezone

import pytest
from pymongo.database import Database

from space_weather_analysis.metadata.models import (
    CitationInfo,
    DatasetMetadata,
    MetadataSource,
    RawMetadataRecord,
    SkippedParameter,
    SourceInfo,
    TimeInfo,
    VariableMetadata,
)
from space_weather_analysis.metadata.repository import MongoMetadataRepository

RETRIEVED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _record(dataset_id: str, collection: str) -> DatasetMetadata:
    """Builds a record with values in every section of the schema."""
    return DatasetMetadata(
        dataset_id=dataset_id,
        collection=collection,
        source=SourceInfo(type="HAPI", server_url="https://hapi.example.org/hapi"),
        description="Synthetic dataset",
        time=TimeInfo(coverage_start=datetime(2024, 1, 1, tzinfo=timezone.utc), cadence="PT1M", timestamp_location="begin"),
        citation=CitationInfo(doi="10.5880/Synthetic.1", license="CC BY 4.0"),
        variables=[
            VariableMetadata.from_single_source("hapi", name="B_x", source_name="B", component="x", units="nT", fill="-1e31", vector_component="x"),
            VariableMetadata.from_single_source("curated", name="status", source_name="status", dtype="string", flag_meanings={"def": "definitive"}),
        ],
        not_stored=[SkippedParameter(name="spectrum", reason="array with more than 10 elements")],
        provenance=[MetadataSource(kind="hapi", url="https://hapi.example.org/hapi", retrieved_at=RETRIEVED_AT)],
        updated_at=RETRIEVED_AT,
    )


@pytest.mark.integration
def test_record_round_trip_through_repository(db: Database) -> None:
    """A saved record reads back unchanged by dataset ID and is stored with the dataset ID as _id."""
    repository = MongoMetadataRepository(db)
    record = _record("synthetic/one", "synthetic_one")

    repository.save(record)

    assert repository.get("synthetic/one") == record
    assert db["dataset_metadata"].find_one({"_id": "synthetic/one"}) is not None
    assert repository.get("unknown") is None


@pytest.mark.integration
def test_find_by_collection_returns_all_datasets_of_the_collection(db: Database) -> None:
    """find_by_collection returns the records of every dataset stored in the collection, sorted by dataset ID."""
    repository = MongoMetadataRepository(db)
    repository.save_many([_record("a-b", "a_b"), _record("a_b", "a_b"), _record("other", "other")])

    assert [record.dataset_id for record in repository.find_by_collection("a_b")] == ["a-b", "a_b"]
    assert repository.find_by_collection("missing") == []


@pytest.mark.integration
def test_saving_a_record_again_replaces_it(db: Database) -> None:
    """Saving a record of the same dataset again keeps one document with the latest values."""
    repository = MongoMetadataRepository(db)
    repository.save(_record("synthetic/one", "synthetic_one"))

    repository.save(_record("synthetic/one", "renamed"))

    assert db["dataset_metadata"].count_documents({}) == 1
    stored = repository.get("synthetic/one")
    assert stored is not None
    assert stored.collection == "renamed"


@pytest.mark.integration
def test_raw_metadata_round_trip_and_overwrite_per_dataset_and_kind(db: Database) -> None:
    """Raw metadata is stored once per dataset and kind, a second save replaces it, and other kinds stay separate."""
    repository = MongoMetadataRepository(db)
    repository.save_raw("synthetic/one", "hapi", {"version": 1}, "https://hapi.example.org/hapi", RETRIEVED_AT)
    repository.save_raw("synthetic/one", "istp", {"FILLVAL": -1}, None, RETRIEVED_AT)

    repository.save_raw("synthetic/one", "hapi", {"version": 2}, "https://hapi.example.org/hapi", RETRIEVED_AT)

    assert db["dataset_metadata_raw"].count_documents({}) == 2
    assert repository.get_raw("synthetic/one", "hapi") == RawMetadataRecord(
        dataset_id="synthetic/one", kind="hapi", payload={"version": 2}, url="https://hapi.example.org/hapi", retrieved_at=RETRIEVED_AT
    )
    assert repository.get_raw("synthetic/one", "spase") is None


@pytest.mark.integration
def test_delete_removes_record_and_raw_metadata(db: Database) -> None:
    """Deleting a dataset removes the record and every raw entry of the dataset, and leaves other datasets untouched."""
    repository = MongoMetadataRepository(db)
    repository.save_many([_record("synthetic/one", "synthetic_one"), _record("synthetic/two", "synthetic_two")])
    repository.save_raw("synthetic/one", "hapi", {}, None, RETRIEVED_AT)

    repository.delete("synthetic/one")

    assert repository.get("synthetic/one") is None
    assert repository.get_raw("synthetic/one", "hapi") is None
    assert repository.get("synthetic/two") is not None


@pytest.mark.integration
def test_outdated_documents_are_removed_so_records_can_be_saved(db: Database) -> None:
    """Documents without the collection field are removed, so a record of the same dataset can be saved despite a unique dataset_id index."""
    db["dataset_metadata"].create_index("dataset_id", unique=True)
    db["dataset_metadata"].insert_one({"dataset_id": "synthetic/one", "metadata": {"HAPI": "2.0"}})
    repository = MongoMetadataRepository(db)

    assert repository.remove_outdated_records() == 1
    repository.save(_record("synthetic/one", "synthetic_one"))

    assert db["dataset_metadata"].count_documents({}) == 1
