import os
import pathlib
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pytest
from pymongo.database import Database

from catalog_collector.data_catalog.snapshot import CatalogSnapshotLoader
from catalog_collector.metadata_catalog.snapshot import MetadataSnapshotLoader
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSourceKind, SourceInfo
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.util.error_handling import CatalogError

HAPI_SERVER = "https://hapi.example.org/hapi"
MIRROR_SERVER = "https://mirror.example.org/hapi"
HAPI_DATASET = "synthetic/mag-1"
CDAWEB_DATASET = "SYNTH_K0"
TIME_RANGE = {"start_time": "2024-01-01T00:00Z", "end_time": "2024-02-01T00:00Z"}

CATALOG: dict[str, list[dict[str, Any]]] = {
    HAPI_DATASET: [{"type": "HAPI", "source_id": HAPI_SERVER, "time_range": TIME_RANGE}],
    CDAWEB_DATASET: [{"type": "CDAWEB", "source_id": "SYNTH", "time_range": TIME_RANGE}],
}

type SnapshotFiles = Callable[[dict[str, Any], Any], tuple[pathlib.Path, pathlib.Path]]


def _hapi_info(**dataset_fields: Any) -> dict[str, Any]:
    """Builds a synthetic HAPI /info response with a time parameter and one vector."""
    return {
        "HAPI": "3.1",
        "startDate": "2024-01-01T00:00Z",
        "stopDate": "2024-02-01T00:00Z",
        "parameters": [
            {"name": "Time", "type": "isotime", "units": "UTC", "fill": None, "length": 17},
            {"name": "B", "type": "double", "units": "nT", "fill": "-1e31", "size": [3], "label": ["x", "y", "z"]},
        ],
        **dataset_fields,
    }


def _loader(db: Database, metadata_file: pathlib.Path, clock: Callable[[], datetime]) -> MetadataSnapshotLoader:
    """Builds a metadata snapshot loader on the test database."""
    return MetadataSnapshotLoader(MongoCatalogRepository(db), MongoMetadataRepository(db), metadata_file, clock)


def _load(db: Database, snapshot_files: SnapshotFiles, snapshot: Any, clock: Callable[[], datetime]) -> None:
    """Writes the catalog and the metadata snapshot as files and loads both like the collector."""
    catalog_file, metadata_file = snapshot_files(CATALOG, snapshot)
    CatalogSnapshotLoader(MongoCatalogRepository(db), catalog_file).load_to_db()
    _loader(db, metadata_file, clock).load_metadata_to_db()


@pytest.mark.integration
def test_record_describes_the_first_catalog_server_and_raw_info_keeps_the_response(
    db: Database, snapshot_files: SnapshotFiles, clock: Callable[[], datetime]
) -> None:
    """A snapshot entry yields a record for the dataset's first catalog server, while the raw info keeps the server it came from."""
    info = _hapi_info(x_server_url=MIRROR_SERVER)
    _load(db, snapshot_files, [{"dataset_id": HAPI_DATASET, "metadata": info}], clock)
    repository = MongoMetadataRepository(db)

    record, raw = repository.get(HAPI_DATASET), repository.get_raw(HAPI_DATASET, MetadataSourceKind.HAPI)
    assert record is not None and raw is not None
    assert (record.collection, record.source.server_url, [variable.name for variable in record.variables]) == (
        "synthetic_mag_1",
        HAPI_SERVER,
        ["B_x", "B_y", "B_z"],
    )
    assert (raw.payload, raw.url is not None and raw.url.startswith("file://")) == (info, True)


@pytest.mark.integration
def test_dataset_without_hapi_source_gets_only_raw_info(db: Database, snapshot_files: SnapshotFiles, clock: Callable[[], datetime]) -> None:
    """A snapshot entry of a dataset without HAPI source in the catalog is kept as raw info without normalized record."""
    _load(db, snapshot_files, {CDAWEB_DATASET: _hapi_info()}, clock)
    repository = MongoMetadataRepository(db)

    assert (repository.get(CDAWEB_DATASET), repository.get_raw(CDAWEB_DATASET, MetadataSourceKind.HAPI) is not None) == (None, True)


@pytest.mark.integration
def test_malformed_entries_are_skipped_and_the_rest_loads(db: Database, snapshot_files: SnapshotFiles, clock: Callable[[], datetime]) -> None:
    """Entries without dataset ID or with an info lacking parameters are skipped for the record, while valid entries load."""
    snapshot = [
        {"dataset_id": None, "metadata": None},
        {"dataset_id": HAPI_DATASET, "metadata": {"HAPI": "3.1", "parameters": []}},
        {"dataset_id": "unlisted", "metadata": "not a mapping"},
    ]
    _load(db, snapshot_files, snapshot, clock)
    repository = MongoMetadataRepository(db)

    assert (
        repository.get(HAPI_DATASET),
        repository.get_raw(HAPI_DATASET, MetadataSourceKind.HAPI) is not None,
        db["dataset_metadata_raw"].count_documents({}),
    ) == (
        None,
        True,
        1,
    )


@pytest.mark.integration
def test_snapshot_never_replaces_a_record_of_a_higher_ranked_source(
    db: Database, snapshot_files: SnapshotFiles, clock: Callable[[], datetime]
) -> None:
    """A stored record of another source kind stays in place, while the snapshot still adds the raw info."""
    MongoMetadataRepository(db).save(DatasetMetadata(dataset_id=HAPI_DATASET, collection="synthetic_mag_1", source=SourceInfo(type="CDAWEB")))
    cdaweb_record = MongoMetadataRepository(db).get(HAPI_DATASET)

    _load(db, snapshot_files, [{"dataset_id": HAPI_DATASET, "metadata": _hapi_info()}], clock)

    repository = MongoMetadataRepository(db)
    assert (repository.get(HAPI_DATASET), repository.get_raw(HAPI_DATASET, MetadataSourceKind.HAPI) is not None) == (cdaweb_record, True)


@pytest.mark.integration
def test_snapshot_with_a_newer_file_time_keeps_stored_hapi_metadata(
    db: Database, snapshot_files: SnapshotFiles, clock: Callable[[], datetime]
) -> None:
    """A dataset with stored HAPI metadata keeps it even if the snapshot file was written later, as a checkout sets the file time to now."""
    catalog_file, metadata_file = snapshot_files(CATALOG, [{"dataset_id": HAPI_DATASET, "metadata": _hapi_info(description="first")}])
    CatalogSnapshotLoader(MongoCatalogRepository(db), catalog_file).load_to_db()
    _loader(db, metadata_file, clock).load_metadata_to_db()
    stored = MongoMetadataRepository(db).get_raw(HAPI_DATASET, MetadataSourceKind.HAPI)
    _, newer_file = snapshot_files(CATALOG, [{"dataset_id": HAPI_DATASET, "metadata": _hapi_info(description="stale")}])
    later = newer_file.stat().st_mtime + 86400
    os.utime(newer_file, (later, later))

    _loader(db, newer_file, clock).load_metadata_to_db()

    assert MongoMetadataRepository(db).get_raw(HAPI_DATASET, MetadataSourceKind.HAPI) == stored


@pytest.mark.integration
def test_missing_snapshot_file_raises(db: Database, tmp_path: pathlib.Path, clock: Callable[[], datetime]) -> None:
    """A metadata snapshot file that does not exist raises instead of loading nothing in silence."""
    with pytest.raises(CatalogError):
        _loader(db, tmp_path / "missing.json", clock).load_metadata_to_db()
