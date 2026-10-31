from datetime import UTC, datetime
from typing import Any

import pytest
import requests
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from catalog_collector.cdaweb.datasets import master_file_url
from catalog_collector.metadata_catalog.cdaweb_masters import CdawebMasterRefresher
from catalog_collector.scheduling.models import PlannedRequest
from catalog_collector.worker_tasks.metadata_catalog import CdawebDatasetPlan
from space_weather_analysis.catalog.models import MasterCatalogItem, TimeRangeDict
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.models import DatasetMetadata, SourceInfo, VariableMetadata
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.util.error_handling import DataRetrievalError

CDAWEB_HAPI_URL = "https://cdaweb.gsfc.nasa.gov/hapi"
CDAS_URL = "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys/datasets"
FIDO_DATASET = "SYNTH_K0_FIDO"
SPLIT_DATASET = "SYNTH_H0_MAG"
UNLINKED_DATASET = "SYNTH_K1_NOLINK"
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

# Master files in the CDAWeb JSON layout: lists of single-key mappings, multi-entry attributes as `{index: value}`.
SPLIT_MASTER: dict[str, Any] = {
    "synth_h0_mag_00000000_v01.cdf": {
        "CDFglobalAttributes": [
            {"Acknowledgement": [{"0": "Please acknowledge the "}, {"1": "synthetic team."}]},
            {"DOI": [{"0": "https://doi.org/10.48322/master-doi"}]},
        ],
        "CDFVariables": [
            {"Epoch": [{"VarDescription": [{"DataType": "CDF_TIME_TT2000"}]}, {"VarAttributes": [{"VALIDMIN": "2020-01-01T00:00:00.000"}]}]},
            {
                "B": [
                    {"VarDescription": [{"DataType": "CDF_REAL4"}, {"NumDims": 1}, {"DimSizes": [3]}]},
                    {
                        "VarAttributes": [
                            {"CATDESC": "Master B"},
                            {"UNITS": "nT"},
                            {"VALIDMIN": [-1.0, -2.0, -3.0]},
                            {"VALIDMAX": [1.0, 2.0, 3.0]},
                            {"FILLVAL": -1e31},
                            {"VAR_TYPE": "data"},
                            {"LABL_PTR_1": "label_B"},
                        ]
                    },
                ]
            },
            {
                "label_B": [
                    {"VarDescription": [{"DataType": "CDF_CHAR"}]},
                    {"VarAttributes": [{"VAR_TYPE": "metadata"}]},
                    {"VarData": ["Bx GSE", "By GSE", "Bz GSE"]},
                ]
            },
        ],
    }
}
FIDO_MASTER: dict[str, Any] = {
    "synth_k0_fido_00000000_v01.cdf": {
        "CDFglobalAttributes": [],
        "CDFVariables": [
            {
                "flux": [
                    {"VarDescription": [{"NumDims": 0}]},
                    {"VarAttributes": [{"CATDESC": "Master flux"}, {"UNITS": " "}, {"VALIDMIN": 0}, {"VALIDMAX": 100}]},
                ]
            },
        ],
    }
}


def _master_path(dataset_id: str) -> str:
    """Returns the path under which the fake CDAWeb serves the master file of a dataset."""
    return f"/pub/software/cdawlib/0JSONS/{dataset_id.lower()}_00000000_v01.json"


def _cdas_entry(dataset_id: str, master_url: str | None) -> dict[str, Any]:
    """Builds a synthetic CDAS entry with DOI and, if given, the link to the master file."""
    additional_metadata = [{"Type": "SPDF_SKT_CDF", "value": master_url}] if master_url else []
    return {"Id": dataset_id, "Doi": f"10.48322/{dataset_id.lower()}", "AdditionalMetadata": additional_metadata}


def _store(db: Database, httpserver: HTTPServer) -> None:
    """Saves the catalog, the CDAS entries and the records of the FIDO dataset and the first HAPI split."""
    items: list[MasterCatalogItem] = [
        {"dataset_id": FIDO_DATASET, "sources": [{"type": "FIDO_CDAWEB", "source_id": "SYNTH", "time_range": TIME_RANGE}]},
        {"dataset_id": f"{SPLIT_DATASET}@0", "sources": [{"type": "HAPI", "source_id": CDAWEB_HAPI_URL, "time_range": TIME_RANGE}]},
        {"dataset_id": f"{SPLIT_DATASET}@1", "sources": [{"type": "HAPI", "source_id": CDAWEB_HAPI_URL, "time_range": TIME_RANGE}]},
        {"dataset_id": UNLINKED_DATASET, "sources": [{"type": "FIDO_CDAWEB", "source_id": "SYNTH", "time_range": TIME_RANGE}]},
    ]
    MongoCatalogRepository(db).save_many(items)

    repository = MongoMetadataRepository(db)
    for dataset_id in (FIDO_DATASET, SPLIT_DATASET):
        repository.save_raw(dataset_id, "cdas", _cdas_entry(dataset_id, httpserver.url_for(_master_path(dataset_id))), CDAS_URL, STORED_AT)
    repository.save_raw(UNLINKED_DATASET, "cdas", _cdas_entry(UNLINKED_DATASET, None), CDAS_URL, STORED_AT)

    flux = VariableMetadata.from_single_source("file", name="flux", source_name="flux", description="File flux", units="cm^-3")
    fido_record = DatasetMetadata(dataset_id=FIDO_DATASET, collection=FIDO_DATASET, source=SourceInfo(type="FIDO_CDAWEB"), variables=[flux])
    split_record = build_hapi_metadata(f"{SPLIT_DATASET}@0", SPLIT_DATASET, HAPI_INFO, CDAWEB_HAPI_URL, STORED_AT)
    save_with_overlays(repository, [fido_record, split_record])


SPLIT_DATASET_IDS = [f"{SPLIT_DATASET}@0", f"{SPLIT_DATASET}@1"]


def _refresher(db: Database, session: requests.Session, now: datetime = STORED_AT) -> CdawebMasterRefresher:
    """Builds a master refresher on the test database with a fixed clock."""
    return CdawebMasterRefresher(MongoMetadataRepository(db), session, clock=lambda: now)


def _record(db: Database, dataset_id: str) -> DatasetMetadata:
    """Reads one stored record."""
    record = MongoMetadataRepository(db).get(dataset_id)
    assert record is not None
    return record


def _vector_fields(record: DatasetMetadata) -> list[tuple[Any, ...]]:
    """Lists name, element index, label, valid range, description and label source of each vector element."""
    return [
        (
            variable.name,
            variable.element_index,
            variable.label,
            variable.valid_min,
            variable.valid_max,
            variable.description,
            variable.field_sources["label"],
        )
        for variable in record.variables
    ]


@pytest.mark.integration
def test_plan_lists_one_master_file_per_cdas_dataset_with_a_link(db: Database, httpserver: HTTPServer, http_session: requests.Session) -> None:
    """Both HAPI splits share one request, and a dataset whose CDAS entry links no master file is left out."""
    _store(db, httpserver)

    plan = CdawebDatasetPlan(MongoCatalogRepository(db), MongoMetadataRepository(db), master_file_url)
    planned_requests = plan.plan()

    assert planned_requests == [
        PlannedRequest(SPLIT_DATASET, httpserver.url_for(_master_path(SPLIT_DATASET))),
        PlannedRequest(FIDO_DATASET, httpserver.url_for(_master_path(FIDO_DATASET))),
    ]
    assert plan.dataset_ids(SPLIT_DATASET) == SPLIT_DATASET_IDS


@pytest.mark.integration
def test_master_file_sets_istp_fields_per_vector_element_while_cdas_keeps_its_fields(
    db: Database, httpserver: HTTPServer, http_session: requests.Session
) -> None:
    """Labels and valid ranges come per element from the master file, while the DOI stays the CDAS one."""
    _store(db, httpserver)
    httpserver.expect_request(_master_path(SPLIT_DATASET)).respond_with_json(SPLIT_MASTER)

    changed = _refresher(db, http_session).refresh(SPLIT_DATASET, httpserver.url_for(_master_path(SPLIT_DATASET)), SPLIT_DATASET_IDS)

    record = _record(db, f"{SPLIT_DATASET}@0")
    assert changed is True
    assert _vector_fields(record) == [
        ("B_x", 0, "Bx GSE", -1.0, 1.0, "Master B", "istp"),
        ("B_y", 1, "By GSE", -2.0, 2.0, "Master B", "istp"),
        ("B_z", 2, "Bz GSE", -3.0, 3.0, "Master B", "istp"),
    ]
    assert (record.citation.doi, record.citation.acknowledgement) == ("10.48322/synth_h0_mag", "Please acknowledge the synthetic team.")
    assert [source.kind for source in record.provenance] == ["hapi", "istp", "cdas"]


@pytest.mark.integration
def test_fido_record_takes_master_attributes_only_where_the_master_sets_them(
    db: Database, httpserver: HTTPServer, http_session: requests.Session
) -> None:
    """A blank master unit keeps the unit of the data file, while description and valid range come from the master file."""
    _store(db, httpserver)
    httpserver.expect_request(_master_path(FIDO_DATASET)).respond_with_json(FIDO_MASTER)

    _refresher(db, http_session).refresh(FIDO_DATASET, httpserver.url_for(_master_path(FIDO_DATASET)), [FIDO_DATASET])

    flux = _record(db, FIDO_DATASET).variables[0]
    assert (flux.description, flux.units, flux.valid_min, flux.valid_max) == ("Master flux", "cm^-3", 0.0, 100.0)
    assert flux.field_sources == {
        "name": "file",
        "source_name": "file",
        "description": "istp",
        "units": "file",
        "valid_min": "istp",
        "valid_max": "istp",
    }


@pytest.mark.integration
def test_record_rebuilt_from_hapi_keeps_the_master_fields(db: Database, httpserver: HTTPServer, http_session: requests.Session) -> None:
    """A record rebuilt from the HAPI info and saved by any writer gets the stored master fields again."""
    _store(db, httpserver)
    httpserver.expect_request(_master_path(SPLIT_DATASET)).respond_with_json(SPLIT_MASTER)
    _refresher(db, http_session).refresh(SPLIT_DATASET, httpserver.url_for(_master_path(SPLIT_DATASET)), SPLIT_DATASET_IDS)

    rebuilt = build_hapi_metadata(f"{SPLIT_DATASET}@0", SPLIT_DATASET, HAPI_INFO, CDAWEB_HAPI_URL, STORED_AT)
    save_with_overlays(MongoMetadataRepository(db), [rebuilt])

    assert [variable.label for variable in _record(db, f"{SPLIT_DATASET}@0").variables] == ["Bx GSE", "By GSE", "Bz GSE"]


@pytest.mark.integration
def test_unchanged_master_file_is_not_written_again(db: Database, httpserver: HTTPServer, http_session: requests.Session) -> None:
    """A master file equal to the stored one keeps the stored raw metadata and its retrieval time."""
    _store(db, httpserver)
    httpserver.expect_request(_master_path(SPLIT_DATASET)).respond_with_json(SPLIT_MASTER)
    url = httpserver.url_for(_master_path(SPLIT_DATASET))
    _refresher(db, http_session).refresh(SPLIT_DATASET, url, SPLIT_DATASET_IDS)

    changed = _refresher(db, http_session, datetime(2026, 10, 8, tzinfo=UTC)).refresh(SPLIT_DATASET, url, SPLIT_DATASET_IDS)

    raw_master = MongoMetadataRepository(db).get_raw(SPLIT_DATASET, "istp")
    assert (changed, raw_master is not None and raw_master.retrieved_at) == (False, STORED_AT)


@pytest.mark.integration
def test_overloaded_master_server_leaves_metadata_unchanged_and_names_its_wait(
    db: Database, httpserver: HTTPServer, http_session: requests.Session
) -> None:
    """HTTP 503 with Retry-After stores nothing and passes the wait on to the worker."""
    _store(db, httpserver)
    stored = _record(db, f"{SPLIT_DATASET}@0")
    httpserver.expect_request(_master_path(SPLIT_DATASET)).respond_with_data("busy", status=503, headers={"Retry-After": "60"})

    with pytest.raises(DataRetrievalError) as raised:
        _refresher(db, http_session).refresh(SPLIT_DATASET, httpserver.url_for(_master_path(SPLIT_DATASET)), SPLIT_DATASET_IDS)

    assert (raised.value.retry_after_seconds, MongoMetadataRepository(db).get_raw(SPLIT_DATASET, "istp")) == (60.0, None)
    assert _record(db, f"{SPLIT_DATASET}@0") == stored
