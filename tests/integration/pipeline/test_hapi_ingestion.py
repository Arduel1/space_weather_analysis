import json
import os
import pathlib
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, cast

import pytest
from pymongo.database import Database
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from pipeline_service.pipelines.hapi import HapiPipeline
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import CatalogEntryDict
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.clients.hapi import HapiClient
from space_weather_analysis.coverage.repository import MongoCoverageRepository
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSourceKind
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository

StoredData = Callable[[str, datetime], dict[str, Any]]
StoredTimestampsAndMetadata = Callable[[str], list[tuple[datetime, dict[str, Any]]]]
ServeDataset = Callable[..., str]

DATASET_ID = "synthetic_xray_flux_P1M"
SERVER_PATH = "/hapi"
# The host of the fake HAPI server, the provider ID in the source identifier of the documents.
SERVER_HOST = "localhost"

# Time range of the dataset in the master catalog; one pipeline chunk covers it.
CATALOG_START = "2024-03-01T00:00:00Z"
CATALOG_END = "2024-03-01T06:00:00Z"
RUN_START = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
RUN_END = datetime(2024, 3, 1, 6, 0, tzinfo=UTC)

# Parameters modeled on iSWA and the HAPI reference test data, which write "null" for a missing fill or unit.
XRAY_PARAMETERS: list[dict[str, Any]] = [
    {"name": "Time", "type": "isotime", "units": "UTC", "fill": "null", "length": 20},
    {"name": "Short_Wave", "type": "double", "units": "Watts/m^2", "fill": "0"},
    {"name": "Long_Wave", "type": "double", "units": "Watts/m^2", "fill": "0"},
    {"name": "satellite", "type": "string", "units": "identifier", "fill": "-1"},
    {"name": "quality_flag", "type": "integer", "units": "null", "fill": "-2147483648"},
    {"name": "B_GSM", "type": "double", "units": "nT", "fill": "-9999", "size": [3], "label": ["x", "y", "z"]},
    {"name": "counts", "type": "double", "units": "counts", "fill": "nan", "size": [3]},
    {"name": "flux_matrix", "type": "double", "units": "counts", "fill": None, "size": [2, 2]},
    {"name": "scalariso", "type": "isotime", "units": "UTC", "fill": "0001-01-01T00:00:00Z", "length": 20},
    {"name": "vecstr", "type": "string", "units": None, "fill": "-1", "size": [3]},
]
XRAY_INFO: dict[str, Any] = {
    "HAPI": "3.1",
    "status": {"code": 1200, "message": "OK request successful"},
    "startDate": CATALOG_START,
    "stopDate": CATALOG_END,
    "timeStampLocation": "begin",
    "parameters": XRAY_PARAMETERS,
}

# Invented values; the second row holds the fill value in Short_Wave, satellite, quality_flag, B_GSM y,
# counts 0, scalariso and vecstr 1.
XRAY_CSV = (
    "2024-03-01T00:00:00Z,1.5e-07,2.5e-06,GOES-16,3,1.5,-2.5,3.5,10,20,30,11,12,21,22,2024-03-01T00:00:30Z,A,B,C\n"
    "2024-03-01T00:01:00Z,0,2.6e-06,-1,-2147483648,1.6,-9999,3.6,NaN,21,31,11,12,21,22,0001-01-01T00:00:00Z,D,-1,F\n"
    "2024-03-01T00:02:00Z,1.7e-07,2.7e-06,GOES-18,0,1.7,-2.7,3.7,12,22,32,11,12,21,22,2024-03-01T00:02:30Z,G,H,I\n"
)
REGULAR_TIME = RUN_START
FILL_TIME = REGULAR_TIME + timedelta(minutes=1)
ZERO_FLAG_TIME = REGULAR_TIME + timedelta(minutes=2)
ROW_TIMES = [REGULAR_TIME, FILL_TIME, ZERO_FLAG_TIME]

EPOCH_INFO: dict[str, Any] = {
    **XRAY_INFO,
    "parameters": [
        {"name": "Epoch", "type": "isotime", "units": "UTC", "fill": None, "length": 20},
        {"name": "Short_Wave", "type": "double", "units": "Watts/m^2", "fill": "0"},
    ],
}
EPOCH_CSV = "2024-03-01T00:00:00Z,1.5e-07\n2024-03-01T00:01:00Z,1.6e-07\n"

# A vector and an image above the column limit of 10 elements, which are not stored.
LARGE_ARRAY_INFO: dict[str, Any] = {
    **XRAY_INFO,
    "parameters": [
        {"name": "Time", "type": "isotime", "units": "UTC", "fill": None, "length": 20},
        {"name": "Short_Wave", "type": "double", "units": "Watts/m^2", "fill": "0"},
        {"name": "spectrum", "type": "double", "units": "counts", "fill": None, "size": [12]},
        {"name": "image", "type": "double", "units": "counts", "fill": None, "size": [3, 4]},
    ],
}
LARGE_ARRAY_CSV = "".join(f"2024-03-01T0{hour}:00:00Z,1.5e-07," + ",".join(["1"] * 24) + "\n" for hour in (0, 4))

# Answer of iSWA for a range without data.
NO_DATA_RESPONSE = {"HAPI": "2.0", "status": {"code": 1406, "message": "No data available for the specified ID and time range"}, "data": []}


@pytest.fixture
def serve_dataset(httpserver: HTTPServer) -> ServeDataset:
    """Function letting the local fake HAPI server serve a dataset under a path and returning the server URL."""

    def serve(path: str = SERVER_PATH, info: dict[str, Any] | None = None, data: Response | None = None) -> str:
        if info is None:
            info = XRAY_INFO
        if data is None:
            data = Response(XRAY_CSV, content_type="text/csv")
        httpserver.expect_request(f"{path}/info", query_string={"id": DATASET_ID}).respond_with_json(info)
        httpserver.expect_request(f"{path}/capabilities").respond_with_json({"HAPI": "3.1", "outputFormats": ["csv", "json"]})
        httpserver.expect_request(f"{path}/data").respond_with_response(data)
        return httpserver.url_for(path)

    return serve


def _add_catalog_entry(db: Database, server_urls: list[str], extra_fields: dict[str, Any] | None = None) -> None:
    """Saves the master catalog item listing the given HAPI servers, each with the extra fields, as sources of the dataset."""
    sources = []
    for server_url in server_urls:
        time_range = {"start_time": CATALOG_START, "end_time": CATALOG_END}
        sources.append(cast(CatalogEntryDict, {"type": "HAPI", "source_id": server_url, "time_range": time_range, **(extra_fields or {})}))
    MongoCatalogRepository(db).save({"dataset_id": DATASET_ID, "sources": sources})


def _pipeline(db: Database, chunk_duration: timedelta = timedelta(days=7)) -> HapiPipeline:
    """Builds the HAPI pipeline with a client that asks a failing source again without waiting."""
    return HapiPipeline(
        timeseries=MongoTimeSeriesRepository(db),
        catalog_repository=MongoCatalogRepository(db),
        metadata_repository=MongoMetadataRepository(db),
        coverage_repository=MongoCoverageRepository(db),
        client_factory=partial(HapiClient, retry_backoff_factor=0),
        chunk_duration=chunk_duration,
    )


def _ingest(
    db: Database,
    server_urls: list[str],
    start: datetime | None = RUN_START,
    end: datetime | None = RUN_END,
    extra_fields: dict[str, Any] | None = None,
) -> IngestionReport:
    """Registers the servers with the extra source fields in the catalog, runs the HAPI pipeline for the dataset and returns its report."""
    _add_catalog_entry(db, server_urls, extra_fields)
    return _pipeline(db).run([DATASET_ID], start, end)


def _requested_paths(httpserver: HTTPServer) -> list[str]:
    """Returns the path of every request the fake server received, in order."""
    return [request.path for request, _ in httpserver.log]


def _files_below(directory: pathlib.Path) -> set[pathlib.Path]:
    """Returns every file below a directory, or nothing if it does not exist."""
    found_files = set()
    for root, _, file_names in os.walk(directory):
        for file_name in file_names:
            found_files.add(pathlib.Path(root) / file_name)
    return found_files


@pytest.mark.integration
def test_hapi_ingestion_stores_one_document_per_row_at_its_time(
    db: Database, serve_dataset: ServeDataset, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """Each data row yields one document at its time with the server host as source and the serving server URL."""
    server_url = serve_dataset()

    _ingest(db, [server_url])

    expected_metadata = {"source": f"HAPI/{SERVER_HOST}", "server": server_url}
    assert stored_timestamps_and_metadata(DATASET_ID) == [(time, expected_metadata) for time in ROW_TIMES]


@pytest.mark.integration
def test_hapi_values_and_array_columns_stored_as_given(db: Database, serve_dataset: ServeDataset, stored_data: StoredData) -> None:
    """Vectors get one column per label or index, a 2-D array one per `{name}_{i}_{j}`; integers (also 0), isotimes and strings keep their type."""
    _ingest(db, [serve_dataset()])

    flags = [stored_data(DATASET_ID, time)["quality_flag"] for time in (REGULAR_TIME, ZERO_FLAG_TIME)]
    assert [(type(flag), flag) for flag in flags] == [(int, 3), (int, 0)]
    assert stored_data(DATASET_ID, REGULAR_TIME) == pytest.approx(
        {
            "Short_Wave": 1.5e-07,
            "Long_Wave": 2.5e-06,
            "satellite": "GOES-16",
            "quality_flag": 3,
            "B_GSM_x": 1.5,
            "B_GSM_y": -2.5,
            "B_GSM_z": 3.5,
            "counts_0": 10.0,
            "counts_1": 20.0,
            "counts_2": 30.0,
            "flux_matrix_0_0": 11.0,
            "flux_matrix_0_1": 12.0,
            "flux_matrix_1_0": 21.0,
            "flux_matrix_1_1": 22.0,
            "scalariso": "2024-03-01T00:00:30Z",
            "vecstr_0": "A",
            "vecstr_1": "B",
            "vecstr_2": "C",
        }
    )


@pytest.mark.integration
def test_hapi_fill_values_omit_only_their_fields(db: Database, serve_dataset: ServeDataset, stored_data: StoredData) -> None:
    """Values equal to the fill of their parameter or array element are omitted; the other fields keep their values and types."""
    _ingest(db, [serve_dataset()])

    data = stored_data(DATASET_ID, FILL_TIME)
    assert data == pytest.approx(
        {
            "Long_Wave": 2.6e-06,
            "B_GSM_x": 1.6,
            "B_GSM_z": 3.6,
            "counts_1": 21.0,
            "counts_2": 31.0,
            "flux_matrix_0_0": 11.0,
            "flux_matrix_0_1": 12.0,
            "flux_matrix_1_0": 21.0,
            "flux_matrix_1_1": 22.0,
            "vecstr_0": "D",
            "vecstr_2": "F",
        }
    )
    assert all(isinstance(data[field], float) for field in ("Long_Wave", "B_GSM_x", "counts_1", "flux_matrix_0_0"))


@pytest.mark.integration
def test_hapi_time_parameter_with_other_name_is_ingested(db: Database, serve_dataset: ServeDataset, stored_data: StoredData) -> None:
    """A first parameter named other than 'Time' is used as the time of the documents."""
    server_url = serve_dataset(info=EPOCH_INFO, data=Response(EPOCH_CSV, content_type="text/csv"))

    _ingest(db, [server_url])

    assert db[DATASET_ID].count_documents({}) == 2
    assert stored_data(DATASET_ID, FILL_TIME) == pytest.approx({"Short_Wave": 1.6e-07})


@pytest.mark.integration
def test_hapi_arrays_above_column_limit_are_not_stored(db: Database, httpserver: HTTPServer, stored_data: StoredData) -> None:
    """Arrays with more than 10 elements are left out of the documents of every chunk and listed in the metadata."""
    httpserver.expect_request(f"{SERVER_PATH}/info", query_string={"id": DATASET_ID}).respond_with_json(LARGE_ARRAY_INFO)
    httpserver.expect_request(f"{SERVER_PATH}/data").respond_with_handler(_large_array_rows_in_requested_range)

    _add_catalog_entry(db, [httpserver.url_for(SERVER_PATH)])
    # Two chunks with one row each.
    _pipeline(db, chunk_duration=timedelta(hours=3)).run([DATASET_ID], RUN_START, RUN_END)

    assert db[DATASET_ID].count_documents({}) == 2
    assert stored_data(DATASET_ID, REGULAR_TIME + timedelta(hours=4)) == pytest.approx({"Short_Wave": 1.5e-07})
    assert [skipped.name for skipped in _stored_metadata(db).not_stored] == ["spectrum", "image"]


def _large_array_rows_in_requested_range(request: Request) -> Response:
    """Answers a data request with the rows of LARGE_ARRAY_CSV whose time lies in the requested range."""
    start, stop = request.args["time.min"], request.args["time.max"]
    rows = [row for row in LARGE_ARRAY_CSV.splitlines(keepends=True) if start <= row.split(",")[0] < stop]
    return Response("".join(rows), content_type="text/csv")


@pytest.mark.integration
@pytest.mark.parametrize(
    "response",
    [
        pytest.param(Response(json.dumps(NO_DATA_RESPONSE), status=404, content_type="application/json"), id="404_with_1406"),
        pytest.param(Response("", content_type="text/csv"), id="empty_200"),
    ],
)
def test_hapi_no_data_response_stores_nothing(db: Database, serve_dataset: ServeDataset, response: Response) -> None:
    """A server answering that no data exists leads to no documents and no exception."""
    _ingest(db, [serve_dataset(data=response)])

    assert db[DATASET_ID].count_documents({}) == 0


@pytest.mark.integration
def test_hapi_falls_back_to_next_source_when_first_fails(
    db: Database, serve_dataset: ServeDataset, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """If the first catalog source answers with HTTP 500, the documents come from the second source and name its URL."""
    failing_url = serve_dataset(path="/primary/hapi", data=Response("Internal Server Error", status=500))
    mirror_url = serve_dataset(path="/mirror/hapi")

    _ingest(db, [failing_url, mirror_url])

    expected_metadata = {"source": f"HAPI/{SERVER_HOST}", "server": mirror_url}
    assert stored_timestamps_and_metadata(DATASET_ID) == [(time, expected_metadata) for time in ROW_TIMES]


@pytest.mark.integration
def test_hapi_failing_source_is_asked_again_and_leaves_a_gap(db: Database, serve_dataset: ServeDataset, httpserver: HTTPServer) -> None:
    """A source failing with HTTP 500 is asked three times before its chunk is reported as gap without stored documents."""
    failing_url = serve_dataset(data=Response("Internal Server Error", status=500))

    report = _ingest(db, [failing_url])

    assert [(gap.dataset_id, gap.start, gap.stop) for gap in report.gaps] == [(DATASET_ID, RUN_START, RUN_END)]
    assert (report.chunks_total, _requested_paths(httpserver).count(f"{SERVER_PATH}/data"), db[DATASET_ID].count_documents({})) == (1, 3, 0)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("extra_fields", "expected_stop"),
    [
        pytest.param({}, CATALOG_END, id="ended_source"),
        pytest.param({"ongoing": True}, "2024-03-02T00:00:00Z", id="ongoing_source"),
    ],
)
def test_hapi_request_clipped_to_catalog_time_range(
    db: Database, serve_dataset: ServeDataset, httpserver: HTTPServer, extra_fields: dict[str, Any], expected_stop: str
) -> None:
    """A range wider than the catalog range is requested from the catalog start to the catalog stop, or to the run end if the source is ongoing."""
    server_url = serve_dataset()

    _ingest(
        db,
        [server_url],
        start=datetime(2024, 2, 29, tzinfo=UTC),
        end=datetime(2024, 3, 2, tzinfo=UTC),
        extra_fields=extra_fields,
    )

    data_requests = [request for request, _ in httpserver.log if request.path == f"{SERVER_PATH}/data"]
    assert [(request.args["time.min"], request.args["time.max"]) for request in data_requests] == [(CATALOG_START, expected_stop)]


@pytest.mark.integration
def test_hapi_request_with_unknown_dataset_is_rejected_before_any_request(db: Database, serve_dataset: ServeDataset, httpserver: HTTPServer) -> None:
    """A run naming a dataset without HAPI source in the catalog is rejected before the first request, so the known dataset stays unfetched too."""
    _add_catalog_entry(db, [serve_dataset()])

    with pytest.raises(ValueError, match="no HAPI source"):
        _pipeline(db).run([DATASET_ID, "synthetic_unknown"], RUN_START, RUN_END)

    assert (httpserver.log, db[DATASET_ID].count_documents({})) == ([], 0)


@pytest.mark.integration
def test_hapi_requested_start_kept_when_end_comes_from_catalog(db: Database, serve_dataset: ServeDataset, httpserver: HTTPServer) -> None:
    """A run with a requested start and no end requests from that start to the catalog stop."""
    requested_start = RUN_START + timedelta(hours=1)

    _ingest(db, [serve_dataset()], start=requested_start, end=None)

    data_requests = [request for request, _ in httpserver.log if request.path == f"{SERVER_PATH}/data"]
    assert [(request.args["time.min"], request.args["time.max"]) for request in data_requests] == [("2024-03-01T01:00:00Z", CATALOG_END)]


@pytest.mark.integration
def test_hapi_chunk_costs_one_info_and_one_data_request_without_files(db: Database, serve_dataset: ServeDataset, httpserver: HTTPServer) -> None:
    """One chunk sends exactly one /info and one /data request, no /capabilities request, and writes no hapiclient cache files."""
    cache_dir = pathlib.Path(tempfile.gettempdir()) / "hapi-data"
    files_before = _files_below(cache_dir)

    _ingest(db, [serve_dataset()])

    assert sorted(_requested_paths(httpserver)) == [f"{SERVER_PATH}/data", f"{SERVER_PATH}/info"]
    assert _files_below(cache_dir) == files_before


def _stored_metadata(db: Database) -> DatasetMetadata:
    """Reads the metadata record of the dataset through the repository."""
    record = MongoMetadataRepository(db).get(DATASET_ID)
    assert record is not None
    return record


@pytest.mark.integration
def test_hapi_metadata_variables_match_stored_fields(db: Database, serve_dataset: ServeDataset, stored_data: StoredData) -> None:
    """The record lists one variable per stored field and the time stamp location; the raw info has its server URL beside it, no hapiclient keys."""
    server_url = serve_dataset()
    _ingest(db, [server_url])

    record = _stored_metadata(db)
    assert {variable.name for variable in record.variables} == set(stored_data(DATASET_ID, REGULAR_TIME))
    assert (record.source.type, record.source.server_url, record.collection, record.time.timestamp_location) == (
        "HAPI",
        server_url,
        DATASET_ID,
        "begin",
    )
    raw = MongoMetadataRepository(db).get_raw(DATASET_ID, MetadataSourceKind.HAPI)
    assert raw is not None
    assert raw.payload["parameters"] == XRAY_PARAMETERS
    assert (raw.url, "x_server_url" in raw.payload, "x_dataFile" in raw.payload) == (server_url, False, False)


@pytest.mark.integration
def test_repeated_hapi_ingestion_does_not_duplicate_documents(db: Database, serve_dataset: ServeDataset) -> None:
    """A second run over the same range keeps one document per data row."""
    server_url = serve_dataset()
    _ingest(db, [server_url])

    _pipeline(db).run([DATASET_ID], RUN_START, RUN_END)

    assert db[DATASET_ID].count_documents({}) == len(ROW_TIMES)
