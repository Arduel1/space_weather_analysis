import pathlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
import requests
from pymongo.database import Database

from catalog_collector.cdaweb.datasets import fetch_cdas_datasets
from catalog_collector.data_catalog.snapshot import CatalogSnapshotLoader
from catalog_collector.metadata_catalog.cdas_metadata import CdasMetadataRefresher, CdasMetadataSummary
from catalog_collector.metadata_catalog.snapshot import MetadataSnapshotLoader
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.models import DatasetMetadata, SourceInfo
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MongoMetadataRepository

CDAWEB_HAPI_URL = "https://cdaweb.gsfc.nasa.gov/hapi"
OTHER_HAPI_URL = "https://hapi.example.org/hapi"
FIDO_DATASET = "SYNTH_K0_FIDO"
SPLIT_DATASET = "SYNTH_H0_MAG"
OTHER_DATASET = "synthetic_other_P1M"
TIME_RANGE = {"start_time": "2020-01-01T00:00:00Z", "end_time": "2020-02-01T00:00:00Z"}

CATALOG: dict[str, list[dict[str, Any]]] = {
    FIDO_DATASET: [{"type": "FIDO_CDAWEB", "source_id": "SYNTH", "time_range": TIME_RANGE}],
    f"{SPLIT_DATASET}@0": [{"type": "HAPI", "source_id": CDAWEB_HAPI_URL, "time_range": TIME_RANGE}],
    OTHER_DATASET: [{"type": "HAPI", "source_id": OTHER_HAPI_URL, "time_range": TIME_RANGE}],
}

HAPI_INFO: dict[str, Any] = {
    "HAPI": "3.1",
    "startDate": "2020-01-01T00:00Z",
    "stopDate": "2020-02-01T00:00Z",
    "resourceURL": "https://hapi.example.org/old-notes",
    "parameters": [
        {"name": "Time", "type": "isotime", "units": "UTC", "fill": None, "length": 17},
        {"name": "B", "type": "double", "units": "nT", "fill": "-1e31"},
    ],
}

type ServeCdas = Callable[[list[dict[str, Any]]], str]
type SnapshotFiles = Callable[[dict[str, Any], Any], tuple[pathlib.Path, pathlib.Path]]


def _cdas_entry(dataset_id: str) -> dict[str, Any]:
    """Builds a synthetic CDAS dataset entry with time range, DOI, SPASE resource ID and notes page."""
    return {
        "Id": dataset_id,
        "Label": "Synthetic dataset",
        "TimeInterval": {"Start": "2019-12-31T00:00:00.000Z", "End": "2026-10-01T00:00:00.000Z"},
        "Doi": f"10.48322/{dataset_id.lower()}",
        "SpaseResourceId": f"spase://NASA/NumericalData/Synthetic/{dataset_id}",
        "Notes": f"https://cdaweb.gsfc.nasa.gov/misc/NotesS.html#{dataset_id}",
    }


def _load_snapshots(db: Database, snapshot_files: SnapshotFiles, clock: Callable[[], datetime]) -> None:
    """Loads the catalog and the HAPI snapshot of both HAPI datasets and saves the record of the FIDO dataset."""
    snapshot = [
        {"dataset_id": f"{SPLIT_DATASET}@0", "metadata": {**HAPI_INFO, "x_server_url": CDAWEB_HAPI_URL}},
        {"dataset_id": OTHER_DATASET, "metadata": {**HAPI_INFO, "x_server_url": OTHER_HAPI_URL}},
    ]
    catalog_file, metadata_file = snapshot_files(CATALOG, snapshot)
    CatalogSnapshotLoader(MongoCatalogRepository(db), catalog_file).load_to_db()
    MetadataSnapshotLoader(MongoCatalogRepository(db), MongoMetadataRepository(db), metadata_file, clock).load_metadata_to_db()
    MongoMetadataRepository(db).save(DatasetMetadata(dataset_id=FIDO_DATASET, collection=FIDO_DATASET, source=SourceInfo(type="FIDO_CDAWEB")))


def _refresh(db: Database, session: requests.Session, cdas_url: str, clock: Callable[[], datetime]) -> CdasMetadataSummary:
    """Reads the fake CDAS dataset list and runs one CDAS metadata refresh with it."""
    refresher = CdasMetadataRefresher(MongoCatalogRepository(db), MongoMetadataRepository(db), cdas_url, clock)
    return refresher.refresh(fetch_cdas_datasets(session, cdas_url))


def _cdas_fields(db: Database, dataset_id: str) -> dict[str, Any]:
    """Reads the record fields CDAS overlays, plus the provenance kinds."""
    record = MongoMetadataRepository(db).get(dataset_id)
    assert record is not None
    return {
        "doi": record.citation.doi,
        "spase_resource_id": record.links.spase_resource_id,
        "resource_url": record.links.resource_url,
        "coverage": (record.time.coverage_start, record.time.coverage_stop),
        "provenance": sorted(source.kind for source in record.provenance),
    }


@pytest.mark.integration
def test_overlay_lands_on_cdaweb_records_only(
    db: Database, http_session: requests.Session, serve_cdas: ServeCdas, snapshot_files: SnapshotFiles, clock: Callable[[], datetime]
) -> None:
    """The CDAS fields reach the FIDO record with its coverage and the HAPI split record by its base ID, but no record of another server."""
    _load_snapshots(db, snapshot_files, clock)

    summary = _refresh(db, http_session, serve_cdas([_cdas_entry(dataset_id) for dataset_id in (FIDO_DATASET, SPLIT_DATASET, OTHER_DATASET)]), clock)

    hapi_coverage = (datetime(2020, 1, 1, tzinfo=UTC), datetime(2020, 2, 1, tzinfo=UTC))
    assert (summary.saved, summary.records_overlaid, summary.missing) == (2, 2, 0)
    assert _cdas_fields(db, FIDO_DATASET) == {
        "doi": "10.48322/synth_k0_fido",
        "spase_resource_id": f"spase://NASA/NumericalData/Synthetic/{FIDO_DATASET}",
        "resource_url": f"https://cdaweb.gsfc.nasa.gov/misc/NotesS.html#{FIDO_DATASET}",
        "coverage": (datetime(2019, 12, 31, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)),
        "provenance": ["cdas"],
    }
    assert _cdas_fields(db, f"{SPLIT_DATASET}@0") == {
        "doi": "10.48322/synth_h0_mag",
        "spase_resource_id": f"spase://NASA/NumericalData/Synthetic/{SPLIT_DATASET}",
        "resource_url": f"https://cdaweb.gsfc.nasa.gov/misc/NotesS.html#{SPLIT_DATASET}",
        "coverage": hapi_coverage,
        "provenance": ["cdas", "hapi"],
    }
    assert _cdas_fields(db, OTHER_DATASET) == {
        "doi": None,
        "spase_resource_id": None,
        "resource_url": "https://hapi.example.org/old-notes",
        "coverage": hapi_coverage,
        "provenance": ["hapi"],
    }


@pytest.mark.integration
def test_record_rebuilt_from_its_own_source_keeps_the_cdas_fields(
    db: Database, http_session: requests.Session, serve_cdas: ServeCdas, snapshot_files: SnapshotFiles, clock: Callable[[], datetime], now: datetime
) -> None:
    """A split record rebuilt from its /info response and saved by any writer gets the stored CDAS entry laid over it again."""
    _load_snapshots(db, snapshot_files, clock)
    _refresh(db, http_session, serve_cdas([_cdas_entry(FIDO_DATASET), _cdas_entry(SPLIT_DATASET)]), clock)
    overlaid = _cdas_fields(db, f"{SPLIT_DATASET}@0")

    rebuilt = build_hapi_metadata(f"{SPLIT_DATASET}@0", f"{SPLIT_DATASET}_0", HAPI_INFO, CDAWEB_HAPI_URL, now)
    save_with_overlays(MongoMetadataRepository(db), [rebuilt])

    assert _cdas_fields(db, f"{SPLIT_DATASET}@0") == overlaid


@pytest.mark.integration
def test_unchanged_entries_are_not_rewritten_and_unlisted_ones_kept(
    db: Database, http_session: requests.Session, serve_cdas: ServeCdas, snapshot_files: SnapshotFiles, clock: Callable[[], datetime]
) -> None:
    """A second refresh with the same list stores nothing, and a dataset CDAS stops listing keeps its stored entry."""
    _load_snapshots(db, snapshot_files, clock)
    cdas_url = serve_cdas([_cdas_entry(FIDO_DATASET), _cdas_entry(SPLIT_DATASET)])
    _refresh(db, http_session, cdas_url, clock)
    stored_raw = MongoMetadataRepository(db).get_raw_many([FIDO_DATASET, SPLIT_DATASET], "cdas")

    unchanged = _refresh(db, http_session, cdas_url, clock)
    without_fido = _refresh(db, http_session, serve_cdas([_cdas_entry(SPLIT_DATASET)]), clock)

    assert (unchanged.saved, unchanged.unchanged, without_fido.missing) == (0, 2, 1)
    assert MongoMetadataRepository(db).get_raw_many([FIDO_DATASET, SPLIT_DATASET], "cdas") == stored_raw
