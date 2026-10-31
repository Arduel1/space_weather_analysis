from datetime import UTC, datetime
from typing import Any

import pytest
import requests
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from catalog_collector.cdaweb.masters import parse_master
from catalog_collector.metadata_catalog.spase_records import SpaseRecordRefresher
from catalog_collector.scheduling.models import PlannedRequest
from catalog_collector.worker_tasks.metadata_catalog import CdawebDatasetPlan, spase_record_url_of_entry
from space_weather_analysis.catalog.models import MasterCatalogItem, TimeRangeDict
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSourceKind
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MongoMetadataRepository

CDAWEB_HAPI_URL = "https://cdaweb.gsfc.nasa.gov/hapi"
CDAS_URL = "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys/datasets"
SPLIT_DATASET = "SYNTH_H0_MAG"
NO_SPASE_DATASET = "SYNTH_K1_NOSPASE"
SPASE_ID = "spase://NASA/NumericalData/SYNTH/MAG/PT1M"
SPASE_PATH = "/NASA/NumericalData/SYNTH/MAG/PT1M.json"
TIME_RANGE: TimeRangeDict = {"start_time": "2020-01-01T00:00:00Z", "end_time": "2020-02-01T00:00:00Z"}
STORED_AT = datetime(2026, 10, 1, tzinfo=UTC)

HAPI_INFO: dict[str, Any] = {
    "HAPI": "3.1",
    "startDate": "2020-01-01T00:00Z",
    "stopDate": "2020-02-01T00:00Z",
    "parameters": [
        {"name": "Time", "type": "isotime", "units": "UTC", "fill": None, "length": 17},
        {"name": "B", "type": "double", "units": "nT", "fill": "-1e31", "size": [3], "label": ["x", "y", "z"], "description": "HAPI B"},
    ],
}

# A SPASE record in the JSON form of spase-metadata.org: numbers as text, repeated elements as lists, 1-based element indices.
SPASE_RECORD: dict[str, Any] = {
    "Spase": {
        "Version": "2.7.2",
        "NumericalData": {
            "ResourceID": SPASE_ID,
            "TemporalDescription": {"TimeSpan": {"StartDate": "2020-01-01T00:00:00Z", "RelativeStopDate": "-P2M"}, "Cadence": "PT1M"},
            "Parameter": [
                {
                    "Name": "Time",
                    "ParameterKey": "Epoch",
                    "Units": "ms",
                    "ValidMin": "1996-01-01T00:00:00.000Z",
                    "FillValue": "-1.0E31",
                    "Support": {"SupportQuantity": "Temporal"},
                },
                {
                    "Name": "B GSE",
                    "ParameterKey": "B",
                    "Description": "SPASE B",
                    "Units": "nT",
                    "CoordinateSystem": {"CoordinateRepresentation": "Cartesian", "CoordinateSystemName": "GSE"},
                    "Structure": {
                        "Size": "3",
                        "Element": [
                            {
                                "Name": "Bx GSE",
                                "Qualifier": "Component.I",
                                "Index": "1",
                                "ValidMin": "-10.0",
                                "ValidMax": "10.0",
                                "FillValue": "-1.0E31",
                            },
                            {
                                "Name": "By GSE",
                                "Qualifier": "Component.J",
                                "Index": "2",
                                "ValidMin": "-20.0",
                                "ValidMax": "20.0",
                                "FillValue": "-1.0E31",
                            },
                            {
                                "Name": "Bz GSE",
                                "Qualifier": "Component.K",
                                "Index": "3",
                                "ValidMin": "-30.0",
                                "ValidMax": "30.0",
                                "FillValue": "-1.0E31",
                            },
                        ],
                    },
                    "Field": {"Qualifier": "Vector", "FieldQuantity": "Magnetic"},
                },
            ],
        },
    }
}

# A master file in the CDAWeb JSON layout whose labels and valid ranges differ from the SPASE record.
MASTER: dict[str, Any] = {
    "synth_h0_mag_00000000_v01.cdf": {
        "CDFglobalAttributes": [],
        "CDFVariables": [
            {
                "B": [
                    {"VarDescription": [{"NumDims": 1}, {"DimSizes": [3]}]},
                    {"VarAttributes": [{"VALIDMIN": [-1.0, -2.0, -3.0]}, {"VALIDMAX": [1.0, 2.0, 3.0]}, {"LABL_PTR_1": "label_B"}]},
                ]
            },
            {
                "label_B": [
                    {"VarDescription": []},
                    {"VarAttributes": [{"VAR_TYPE": "metadata"}]},
                    {"VarData": ["Bx master", "By master", "Bz master"]},
                ]
            },
        ],
    }
}


def _store(db: Database) -> None:
    """Saves the catalog with two HAPI splits and a dataset without SPASE ID, their CDAS entries and the record of the first split."""
    items: list[MasterCatalogItem] = [
        {"dataset_id": f"{SPLIT_DATASET}@0", "sources": [{"type": "HAPI", "source_id": CDAWEB_HAPI_URL, "time_range": TIME_RANGE}]},
        {"dataset_id": f"{SPLIT_DATASET}@1", "sources": [{"type": "HAPI", "source_id": CDAWEB_HAPI_URL, "time_range": TIME_RANGE}]},
        {"dataset_id": NO_SPASE_DATASET, "sources": [{"type": "CDAWEB", "source_id": "SYNTH", "time_range": TIME_RANGE}]},
    ]
    MongoCatalogRepository(db).save_many(items)
    repository = MongoMetadataRepository(db)
    repository.save_raw(SPLIT_DATASET, MetadataSourceKind.CDAS, {"Id": SPLIT_DATASET, "SpaseResourceId": SPASE_ID}, CDAS_URL, STORED_AT)
    repository.save_raw(NO_SPASE_DATASET, MetadataSourceKind.CDAS, {"Id": NO_SPASE_DATASET}, CDAS_URL, STORED_AT)
    save_with_overlays(repository, [build_hapi_metadata(f"{SPLIT_DATASET}@0", SPLIT_DATASET, HAPI_INFO, CDAWEB_HAPI_URL, STORED_AT)])


SPLIT_DATASET_IDS = [f"{SPLIT_DATASET}@0", f"{SPLIT_DATASET}@1"]


def _refresher(db: Database, session: requests.Session, now: datetime = STORED_AT) -> SpaseRecordRefresher:
    """Builds a SPASE refresher on the test database with a fixed clock."""
    return SpaseRecordRefresher(MongoMetadataRepository(db), session, clock=lambda: now)


def _record(db: Database) -> DatasetMetadata:
    """Reads the stored record of the first HAPI split."""
    record = MongoMetadataRepository(db).get(f"{SPLIT_DATASET}@0")
    assert record is not None
    return record


def _vector_fields(record: DatasetMetadata) -> list[tuple[Any, ...]]:
    """Lists name, label, valid range, coordinate system, vector component and the sources of label and coordinate system of each element."""
    return [
        (
            variable.name,
            variable.label,
            variable.valid_min,
            variable.valid_max,
            variable.coordinate_system,
            variable.vector_component,
            variable.field_sources["label"],
            variable.field_sources["coordinate_system"],
        )
        for variable in record.variables
    ]


@pytest.mark.integration
def test_plan_lists_one_spase_record_per_cdas_dataset_with_a_spase_id(db: Database) -> None:
    """Both HAPI splits share one request at spase-metadata.org, and a dataset whose CDAS entry names no SPASE ID is left out."""
    _store(db)

    planned_requests = CdawebDatasetPlan(MongoCatalogRepository(db), MongoMetadataRepository(db), spase_record_url_of_entry).plan()

    assert planned_requests == [PlannedRequest(SPLIT_DATASET, f"https://spase-metadata.org{SPASE_PATH}")]


@pytest.mark.integration
def test_spase_record_sets_coordinate_system_vector_components_and_cadence(
    db: Database, httpserver: HTTPServer, http_session: requests.Session
) -> None:
    """Without master file, labels, valid ranges, coordinate system and Cartesian components come per element from SPASE."""
    _store(db)
    httpserver.expect_request(SPASE_PATH).respond_with_json(SPASE_RECORD)

    changed = _refresher(db, http_session).refresh(SPLIT_DATASET, httpserver.url_for(SPASE_PATH), SPLIT_DATASET_IDS)

    record = _record(db)
    assert changed is True
    assert _vector_fields(record) == [
        ("B_x", "Bx GSE", -10.0, 10.0, "GSE", "x", "spase", "spase"),
        ("B_y", "By GSE", -20.0, 20.0, "GSE", "y", "spase", "spase"),
        ("B_z", "Bz GSE", -30.0, 30.0, "GSE", "z", "spase", "spase"),
    ]
    assert (record.time.cadence, record.variables[0].description, [source.kind for source in record.provenance]) == (
        "PT1M",
        "SPASE B",
        ["hapi", "spase", "cdas"],
    )


@pytest.mark.integration
def test_master_file_fields_rank_above_the_spase_record(db: Database, httpserver: HTTPServer, http_session: requests.Session) -> None:
    """With a stored master file, labels and valid ranges stay the ISTP ones, while SPASE adds the coordinate system."""
    _store(db)
    MongoMetadataRepository(db).save_raw(
        SPLIT_DATASET, MetadataSourceKind.ISTP, parse_master(MASTER), "https://cdaweb.example.org/master.json", STORED_AT
    )
    httpserver.expect_request(SPASE_PATH).respond_with_json(SPASE_RECORD)

    _refresher(db, http_session).refresh(SPLIT_DATASET, httpserver.url_for(SPASE_PATH), SPLIT_DATASET_IDS)

    assert _vector_fields(_record(db)) == [
        ("B_x", "Bx master", -1.0, 1.0, "GSE", "x", "istp", "spase"),
        ("B_y", "By master", -2.0, 2.0, "GSE", "y", "istp", "spase"),
        ("B_z", "Bz master", -3.0, 3.0, "GSE", "z", "istp", "spase"),
    ]


@pytest.mark.integration
def test_missing_spase_record_stores_nothing(db: Database, httpserver: HTTPServer, http_session: requests.Session) -> None:
    """A SPASE ID without record (HTTP 404) changes nothing and is no failure."""
    _store(db)
    stored = _record(db)
    httpserver.expect_request(SPASE_PATH).respond_with_data("not found", status=404)

    changed = _refresher(db, http_session).refresh(SPLIT_DATASET, httpserver.url_for(SPASE_PATH), SPLIT_DATASET_IDS)

    assert (changed, MongoMetadataRepository(db).get_raw(SPLIT_DATASET, MetadataSourceKind.SPASE), _record(db)) == (False, None, stored)


@pytest.mark.integration
def test_unchanged_spase_record_is_not_written_again(db: Database, httpserver: HTTPServer, http_session: requests.Session) -> None:
    """A SPASE record equal to the stored one keeps the stored raw metadata and its retrieval time."""
    _store(db)
    httpserver.expect_request(SPASE_PATH).respond_with_json(SPASE_RECORD)
    url = httpserver.url_for(SPASE_PATH)
    _refresher(db, http_session).refresh(SPLIT_DATASET, url, SPLIT_DATASET_IDS)

    changed = _refresher(db, http_session, datetime(2026, 10, 8, tzinfo=UTC)).refresh(SPLIT_DATASET, url, SPLIT_DATASET_IDS)

    raw_record = MongoMetadataRepository(db).get_raw(SPLIT_DATASET, MetadataSourceKind.SPASE)
    assert (changed, raw_record is not None and raw_record.retrieved_at) == (False, STORED_AT)


@pytest.mark.integration
def test_record_rebuilt_from_hapi_keeps_the_spase_fields(db: Database, httpserver: HTTPServer, http_session: requests.Session) -> None:
    """A record rebuilt from the HAPI info and saved by any writer gets the stored SPASE fields again."""
    _store(db)
    httpserver.expect_request(SPASE_PATH).respond_with_json(SPASE_RECORD)
    _refresher(db, http_session).refresh(SPLIT_DATASET, httpserver.url_for(SPASE_PATH), SPLIT_DATASET_IDS)

    rebuilt = build_hapi_metadata(f"{SPLIT_DATASET}@0", SPLIT_DATASET, HAPI_INFO, CDAWEB_HAPI_URL, STORED_AT)
    save_with_overlays(MongoMetadataRepository(db), [rebuilt])

    assert [(variable.coordinate_system, variable.vector_component) for variable in _record(db).variables] == [
        ("GSE", "x"),
        ("GSE", "y"),
        ("GSE", "z"),
    ]
