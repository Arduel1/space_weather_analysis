import json
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest
import requests
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from catalog_collector.data_catalog.snapshot import CatalogSnapshotLoader
from catalog_collector.metadata_catalog.cdas_metadata import CdasMetadataRefresher, CdasMetadataSummary
from catalog_collector.metadata_catalog.snapshot import MetadataSnapshotLoader
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.models import DatasetMetadata, SourceInfo
from space_weather_analysis.metadata.repository import MongoMetadataRepository

CDAS_PATH = "/WS/cdasr/1/dataviews/sp_phys/datasets"
CDAWEB_HAPI_URL = "https://cdaweb.gsfc.nasa.gov/hapi"
OTHER_HAPI_URL = "https://hapi.example.org/hapi"
FIDO_DATASET = "SYNTH_K0_FIDO"
SPLIT_DATASET = "SYNTH_H0_MAG"
OTHER_DATASET = "synthetic_other_P1M"
TIME_RANGE = {"start_time": "2020-01-01T00:00:00Z", "end_time": "2020-02-01T00:00:00Z"}

CATALOG: Dict[str, List[Dict[str, Any]]] = {
    FIDO_DATASET: [{"type": "FIDO_CDAWEB", "source_id": "SYNTH", "time_range": TIME_RANGE}],
    f"{SPLIT_DATASET}@0": [{"type": "HAPI", "source_id": CDAWEB_HAPI_URL, "time_range": TIME_RANGE}],
    OTHER_DATASET: [{"type": "HAPI", "source_id": OTHER_HAPI_URL, "time_range": TIME_RANGE}],
}

HAPI_INFO: Dict[str, Any] = {
    "HAPI": "3.1",
    "startDate": "2020-01-01T00:00Z",
    "stopDate": "2020-02-01T00:00Z",
    "resourceURL": "https://hapi.example.org/old-notes",
    "parameters": [
        {"name": "Time", "type": "isotime", "units": "UTC", "fill": None, "length": 17},
        {"name": "B", "type": "double", "units": "nT", "fill": "-1e31"},
    ],
}


def _cdas_entry(dataset_id: str) -> Dict[str, Any]:
    """Builds a synthetic CDAS dataset entry with time range, DOI, SPASE resource ID and notes page."""
    return {
        "Id": dataset_id,
        "Label": "Synthetic dataset",
        "TimeInterval": {"Start": "2019-12-31T00:00:00.000Z", "End": "2026-10-01T00:00:00.000Z"},
        "Doi": f"10.48322/{dataset_id.lower()}",
        "SpaseResourceId": f"spase://NASA/NumericalData/Synthetic/{dataset_id}",
        "Notes": f"https://cdaweb.gsfc.nasa.gov/misc/NotesS.html#{dataset_id}",
    }


def _serve_cdas(httpserver: HTTPServer, dataset_ids: List[str]) -> str:
    """Lets the fake CDAS serve its dataset list with an entry per dataset ID and returns its URL."""
    httpserver.clear()
    entries = [_cdas_entry(dataset_id) for dataset_id in dataset_ids]
    httpserver.expect_request(CDAS_PATH).respond_with_json({"DatasetDescription": entries})
    return httpserver.url_for(CDAS_PATH)


def _load_snapshots(db: Database, tmp_path: pathlib.Path) -> MetadataSnapshotLoader:
    """Loads the catalog and the HAPI snapshot of both HAPI datasets and returns the metadata loader."""
    catalog_file = tmp_path / "master_catalog.json"
    catalog_file.write_text(json.dumps(CATALOG))
    metadata_file = tmp_path / "dataset_metadata.json"
    snapshot = [
        {"dataset_id": f"{SPLIT_DATASET}@0", "metadata": {**HAPI_INFO, "x_server_url": CDAWEB_HAPI_URL}},
        {"dataset_id": OTHER_DATASET, "metadata": {**HAPI_INFO, "x_server_url": OTHER_HAPI_URL}},
    ]
    metadata_file.write_text(json.dumps(snapshot))
    CatalogSnapshotLoader(db, catalog_file).load_to_db()
    metadata_loader = MetadataSnapshotLoader(db, metadata_file)
    metadata_loader.load_metadata_to_db()
    MongoMetadataRepository(db).save(DatasetMetadata(dataset_id=FIDO_DATASET, collection=FIDO_DATASET, source=SourceInfo(type="FIDO_CDAWEB")))
    return metadata_loader


def _refresh(db: Database, cdas_url: str) -> CdasMetadataSummary:
    """Runs one CDAS metadata refresh against the fake CDAS and returns its summary."""
    return CdasMetadataRefresher(MongoCatalogRepository(db), MongoMetadataRepository(db), requests.Session(), cdas_url).refresh()


def _cdas_fields(db: Database, dataset_id: str) -> Dict[str, Optional[Any]]:
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
def test_overlay_lands_on_cdaweb_records_only(db: Database, httpserver: HTTPServer, tmp_path: pathlib.Path) -> None:
    """The CDAS fields reach the Fido record with its coverage and the HAPI split record by its base ID, but no record of another server."""
    _load_snapshots(db, tmp_path)

    summary = _refresh(db, _serve_cdas(httpserver, [FIDO_DATASET, SPLIT_DATASET, OTHER_DATASET]))

    hapi_coverage = (datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2020, 2, 1, tzinfo=timezone.utc))
    assert (summary.saved, summary.records_overlaid, summary.missing) == (2, 2, 0)
    assert _cdas_fields(db, FIDO_DATASET) == {
        "doi": "10.48322/synth_k0_fido",
        "spase_resource_id": f"spase://NASA/NumericalData/Synthetic/{FIDO_DATASET}",
        "resource_url": f"https://cdaweb.gsfc.nasa.gov/misc/NotesS.html#{FIDO_DATASET}",
        "coverage": (datetime(2019, 12, 31, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc)),
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
def test_record_rebuilt_from_its_own_source_keeps_the_cdas_fields(db: Database, httpserver: HTTPServer, tmp_path: pathlib.Path) -> None:
    """Reloading the HAPI snapshot rebuilds the split record from its /info response and lays the stored CDAS entry over it again."""
    metadata_loader = _load_snapshots(db, tmp_path)
    _refresh(db, _serve_cdas(httpserver, [FIDO_DATASET, SPLIT_DATASET]))
    overlaid = _cdas_fields(db, f"{SPLIT_DATASET}@0")

    metadata_loader.load_metadata_to_db()

    assert _cdas_fields(db, f"{SPLIT_DATASET}@0") == overlaid


@pytest.mark.integration
def test_unchanged_entries_are_not_rewritten_and_unlisted_ones_kept(db: Database, httpserver: HTTPServer, tmp_path: pathlib.Path) -> None:
    """A second refresh with the same list stores nothing, and a dataset CDAS stops listing keeps its stored entry."""
    _load_snapshots(db, tmp_path)
    cdas_url = _serve_cdas(httpserver, [FIDO_DATASET, SPLIT_DATASET])
    _refresh(db, cdas_url)
    stored_raw = MongoMetadataRepository(db).get_raw_many([FIDO_DATASET, SPLIT_DATASET], "cdas")

    unchanged = _refresh(db, cdas_url)
    without_fido = _refresh(db, _serve_cdas(httpserver, [SPLIT_DATASET]))

    assert (unchanged.saved, unchanged.unchanged, without_fido.missing) == (0, 2, 1)
    assert MongoMetadataRepository(db).get_raw_many([FIDO_DATASET, SPLIT_DATASET], "cdas") == stored_raw
