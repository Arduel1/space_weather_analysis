import json
import logging
import os
import pathlib
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pytest
from pymongo.database import Database
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from pipeline_service.pipelines.hapi import HapiPipeline

StoredData = Callable[[str, datetime], Dict[str, Any]]
StoredTimestampsAndMetadata = Callable[[str], List[Tuple[datetime, Dict[str, Any]]]]
ServeDataset = Callable[..., str]

DATASET_ID = "synthetic_xray_flux_P1M"
SERVER_PATH = "/hapi"

# Time range of the dataset in the master catalog; one pipeline chunk covers it.
CATALOG_START = "2024-03-01T00:00:00Z"
CATALOG_END = "2024-03-01T06:00:00Z"
RUN_START = datetime(2024, 3, 1, 0, 0, tzinfo=timezone.utc)
RUN_END = datetime(2024, 3, 1, 6, 0, tzinfo=timezone.utc)

# Parameters modeled on iSWA and the HAPI reference test data, which write "null" for a missing fill or unit.
XRAY_PARAMETERS: List[Dict[str, Any]] = [
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
XRAY_INFO: Dict[str, Any] = {
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
REGULAR_TIME = datetime(2024, 3, 1, 0, 0)
FILL_TIME = datetime(2024, 3, 1, 0, 1)
ZERO_FLAG_TIME = datetime(2024, 3, 1, 0, 2)
ROW_TIMES = [REGULAR_TIME, FILL_TIME, ZERO_FLAG_TIME]

EPOCH_INFO: Dict[str, Any] = {
    **XRAY_INFO,
    "parameters": [
        {"name": "Epoch", "type": "isotime", "units": "UTC", "fill": None, "length": 20},
        {"name": "Short_Wave", "type": "double", "units": "Watts/m^2", "fill": "0"},
    ],
}
EPOCH_CSV = "2024-03-01T00:00:00Z,1.5e-07\n2024-03-01T00:01:00Z,1.6e-07\n"

# A vector and an image above the column limit of 10 elements, which are not stored.
LARGE_ARRAY_INFO: Dict[str, Any] = {
    **XRAY_INFO,
    "parameters": [
        {"name": "Time", "type": "isotime", "units": "UTC", "fill": None, "length": 20},
        {"name": "Short_Wave", "type": "double", "units": "Watts/m^2", "fill": "0"},
        {"name": "spectrum", "type": "double", "units": "counts", "fill": None, "size": [12]},
        {"name": "image", "type": "double", "units": "counts", "fill": None, "size": [3, 4]},
    ],
}
LARGE_ARRAY_CSV = "".join(f"2024-03-01T0{hour}:00:00Z,1.5e-07," + ",".join(["1"] * 24) + "\n" for hour in (0, 4))

# Loggers of the HAPI client and pipeline.
HAPI_LOGGER_NAMES = {"space_weather_analysis.clients.hapi", "pipeline_service.pipelines.hapi"}

# Answer of iSWA for a range without data.
NO_DATA_RESPONSE = {"HAPI": "2.0", "status": {"code": 1406, "message": "No data available for the specified ID and time range"}, "data": []}


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries of the HAPI client without waiting time."""
    monkeypatch.setattr("space_weather_analysis.util.resilience.time.sleep", lambda seconds: None)


@pytest.fixture
def serve_dataset(httpserver: HTTPServer) -> ServeDataset:
    """Function letting the local fake HAPI server serve a dataset under a path and returning the server URL."""

    def serve(path: str = SERVER_PATH, info: Optional[Dict[str, Any]] = None, data: Optional[Response] = None) -> str:
        if info is None:
            info = XRAY_INFO
        if data is None:
            data = Response(XRAY_CSV, content_type="text/csv")
        httpserver.expect_request(f"{path}/info", query_string={"id": DATASET_ID}).respond_with_json(info)
        httpserver.expect_request(f"{path}/capabilities").respond_with_json({"HAPI": "3.1", "outputFormats": ["csv", "json"]})
        httpserver.expect_request(f"{path}/data").respond_with_response(data)
        return httpserver.url_for(path)

    return serve


def _add_catalog_entry(db: Database, server_urls: List[str]) -> None:
    """Inserts the master catalog document listing the given HAPI servers as sources of the dataset."""
    sources = []
    for server_url in server_urls:
        sources.append({"type": "HAPI", "source_id": server_url, "time_range": {"start_time": CATALOG_START, "end_time": CATALOG_END}})
    db["master_catalog"].insert_one({"dataset_id": DATASET_ID, "sources": sources})


def _ingest(db: Database, tmp_path: pathlib.Path, server_urls: List[str], start: datetime = RUN_START, end: datetime = RUN_END) -> None:
    """Registers the servers in the catalog and runs the HAPI pipeline for the dataset."""
    _add_catalog_entry(db, server_urls)
    HapiPipeline(db, tmp_path).run([DATASET_ID], start, end)


def _requested_paths(httpserver: HTTPServer) -> List[str]:
    """Returns the path of every request the fake server received, in order."""
    return [request.path for request, _ in httpserver.log]


def _files_below(directory: pathlib.Path) -> Set[pathlib.Path]:
    """Returns every file below a directory, or nothing if it does not exist."""
    found_files = set()
    for root, _, file_names in os.walk(directory):
        for file_name in file_names:
            found_files.add(pathlib.Path(root) / file_name)
    return found_files


@pytest.mark.integration
def test_hapi_ingestion_stores_one_document_per_row_at_its_time(
    db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """Each data row yields one document at its time (naive UTC) with the serving server URL as source."""
    server_url = serve_dataset()

    _ingest(db, tmp_path, [server_url])

    # pymongo returns stored datetimes as naive UTC, so the expected times carry no time zone.
    assert stored_timestamps_and_metadata(DATASET_ID) == [(time, {"source": server_url}) for time in ROW_TIMES]


@pytest.mark.integration
def test_hapi_values_and_array_columns_stored_as_given(
    db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset, stored_data: StoredData
) -> None:
    """Vectors get one column per label or index, a 2-D array one column per <name>_<i>_<j>, isotime and string values stay strings."""
    _ingest(db, tmp_path, [serve_dataset()])

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
def test_hapi_fill_values_omit_only_their_fields(db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset, stored_data: StoredData) -> None:
    """Values equal to the fill of their parameter or array element are omitted; the other fields keep their values and types."""
    _ingest(db, tmp_path, [serve_dataset()])

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
def test_hapi_integer_parameter_stored_as_int(db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset, stored_data: StoredData) -> None:
    """Integer values are stored as integers, including 0, while another row holds the integer fill."""
    _ingest(db, tmp_path, [serve_dataset()])

    flags = [stored_data(DATASET_ID, time)["quality_flag"] for time in (REGULAR_TIME, ZERO_FLAG_TIME)]
    assert flags == [3, 0]
    assert all(type(flag) is int for flag in flags)


@pytest.mark.integration
def test_hapi_time_parameter_with_other_name_is_ingested(
    db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset, stored_data: StoredData
) -> None:
    """A first parameter named other than 'Time' is used as the time of the documents."""
    server_url = serve_dataset(info=EPOCH_INFO, data=Response(EPOCH_CSV, content_type="text/csv"))

    _ingest(db, tmp_path, [server_url])

    assert db[DATASET_ID].count_documents({}) == 2
    assert stored_data(DATASET_ID, datetime(2024, 3, 1, 0, 1)) == pytest.approx({"Short_Wave": 1.6e-07})


@pytest.mark.integration
def test_hapi_arrays_above_column_limit_are_not_stored(
    db: Database, tmp_path: pathlib.Path, httpserver: HTTPServer, stored_data: StoredData, caplog: pytest.LogCaptureFixture
) -> None:
    """Arrays with more than 10 elements are left out of the documents, listed in the metadata and warned about once."""
    caplog.set_level(logging.INFO)
    httpserver.expect_request(f"{SERVER_PATH}/info", query_string={"id": DATASET_ID}).respond_with_json(LARGE_ARRAY_INFO)
    httpserver.expect_request(f"{SERVER_PATH}/data").respond_with_handler(_large_array_rows_in_requested_range)

    # Two chunks with one row each, so the warning must not repeat per chunk.
    _add_catalog_entry(db, [httpserver.url_for(SERVER_PATH)])
    HapiPipeline(db, tmp_path).run([DATASET_ID], RUN_START, RUN_END, chunk_delta=timedelta(hours=3))

    assert db[DATASET_ID].count_documents({}) == 2
    assert stored_data(DATASET_ID, datetime(2024, 3, 1, 4, 0)) == pytest.approx({"Short_Wave": 1.5e-07})
    metadata = db["dataset_metadata"].find_one({"_id": DATASET_ID})
    assert metadata is not None
    assert metadata["source_info"]["Parameters Not Stored"] == ["spectrum", "image"]
    skip_warnings = [record for record in caplog.records if record.levelno == logging.WARNING and "not stored" in record.getMessage()]
    assert len(skip_warnings) == 1


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
def test_hapi_no_data_response_stores_nothing(db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset, response: Response) -> None:
    """A server answering that no data exists leads to no documents and no exception."""
    _ingest(db, tmp_path, [serve_dataset(data=response)])

    assert db[DATASET_ID].count_documents({}) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("response", "expect_warning"),
    [
        pytest.param(Response(json.dumps(NO_DATA_RESPONSE), status=404, content_type="application/json"), False, id="404_with_1406"),
        pytest.param(Response("Internal Server Error", status=500), True, id="500_without_hapi_status"),
    ],
)
def test_hapi_warns_only_when_a_request_fails(
    db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset, caplog: pytest.LogCaptureFixture, response: Response, expect_warning: bool
) -> None:
    """A failed request is logged as a warning, while an answer without data is not."""
    caplog.set_level(logging.INFO)

    _ingest(db, tmp_path, [serve_dataset(data=response)])

    hapi_warnings = [record for record in caplog.records if record.levelno >= logging.WARNING and record.name in HAPI_LOGGER_NAMES]
    assert bool(hapi_warnings) == expect_warning


@pytest.mark.integration
def test_hapi_falls_back_to_next_source_when_first_fails(
    db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """If the first catalog source answers with HTTP 500, the documents come from the second source."""
    failing_url = serve_dataset(path="/primary/hapi", data=Response("Internal Server Error", status=500))
    mirror_url = serve_dataset(path="/mirror/hapi")

    _ingest(db, tmp_path, [failing_url, mirror_url])

    assert stored_timestamps_and_metadata(DATASET_ID) == [(time, {"source": mirror_url}) for time in ROW_TIMES]


@pytest.mark.integration
def test_hapi_request_clipped_to_catalog_time_range(
    db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset, httpserver: HTTPServer
) -> None:
    """A requested range wider than the catalog range is requested from the server only within the catalog range."""
    server_url = serve_dataset()

    _ingest(db, tmp_path, [server_url], start=datetime(2024, 2, 29, tzinfo=timezone.utc), end=datetime(2024, 3, 2, tzinfo=timezone.utc))

    data_requests = [request for request, _ in httpserver.log if request.path == f"{SERVER_PATH}/data"]
    assert [(request.args["time.min"], request.args["time.max"]) for request in data_requests] == [(CATALOG_START, CATALOG_END)]


@pytest.mark.integration
def test_hapi_chunk_costs_one_info_and_one_data_request_without_files(
    db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset, httpserver: HTTPServer
) -> None:
    """One chunk sends exactly one /info and one /data request, no /capabilities request, and writes no hapiclient cache files."""
    cache_dir = pathlib.Path(tempfile.gettempdir()) / "hapi-data"
    files_before = _files_below(cache_dir)

    _ingest(db, tmp_path, [serve_dataset()])

    assert sorted(_requested_paths(httpserver)) == [f"{SERVER_PATH}/data", f"{SERVER_PATH}/info"]
    assert _files_below(cache_dir) == files_before


@pytest.mark.integration
def test_hapi_metadata_stores_null_strings_as_none(db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset) -> None:
    """Units and fill given as the string "null" are stored as None; other fills are kept as given."""
    _ingest(db, tmp_path, [serve_dataset()])

    metadata = db["dataset_metadata"].find_one({"_id": DATASET_ID})
    assert metadata is not None
    variables = {variable["name"]: variable for variable in metadata["variables"]}
    assert (variables["Time"]["fill"], variables["quality_flag"]["units"]) == (None, None)
    assert (variables["satellite"]["fill"], variables["satellite"]["units"]) == ("-1", "identifier")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("info", "expected_location"),
    [
        pytest.param(XRAY_INFO, "begin", id="given"),
        pytest.param({key: value for key, value in XRAY_INFO.items() if key != "timeStampLocation"}, "center", id="absent"),
    ],
)
def test_hapi_metadata_records_time_stamp_location(
    db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset, info: Dict[str, Any], expected_location: str
) -> None:
    """The source info holds the time stamp location of the server, 'center' if the server gives none."""
    _ingest(db, tmp_path, [serve_dataset(info=info)])

    metadata = db["dataset_metadata"].find_one({"_id": DATASET_ID})
    assert metadata is not None
    assert metadata["source_info"]["Time Stamp Location"] == expected_location


@pytest.mark.integration
def test_repeated_hapi_ingestion_does_not_duplicate_documents(db: Database, tmp_path: pathlib.Path, serve_dataset: ServeDataset) -> None:
    """A second run over the same range keeps one document per data row."""
    server_url = serve_dataset()
    _ingest(db, tmp_path, [server_url])

    HapiPipeline(db, tmp_path).run([DATASET_ID], RUN_START, RUN_END)

    assert db[DATASET_ID].count_documents({}) == len(ROW_TIMES)
