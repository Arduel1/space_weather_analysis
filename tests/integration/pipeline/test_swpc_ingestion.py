from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from pipeline_service.pipelines.report import IngestionReport
from pipeline_service.pipelines.swpc import SwpcPipeline
from space_weather_analysis.clients.swpc import SwpcClient
from space_weather_analysis.coverage.repository import MongoCoverageRepository
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository

StoredData = Callable[[str, datetime], dict[str, Any]]
StoredTimestampsAndMetadata = Callable[[str], list[tuple[datetime, dict[str, Any]]]]

XRAY_DATASET = "swpc_goes_xray"
PROTON_DATASET = "swpc_goes_protons"
KP_DATASET = "swpc_kp_estimated"
SERVER_PATH = "/json/"
XRAY_6_HOUR_PATH = "/json/goes/primary/xrays-6-hour.json"
PROTON_6_HOUR_PATH = "/json/goes/primary/integral-protons-6-hour.json"
KP_PATH = "/json/planetary_k_index_1m.json"
GOES_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# SWPC writes the Kp time stamps without zone designator
KP_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"

# Current time of the injected clock, the end of every served file and of every run
NOW = datetime(2026, 11, 3, 18, 30, tzinfo=UTC)
FIRST_MINUTE = NOW - timedelta(minutes=10)
MINUTE = timedelta(minutes=1)
SATELLITE = 18

# Invented flux of the 0.1-0.8 nm and 0.05-0.4 nm channels in W/m^2 for consecutive minutes
LONG_FLUXES = [3.1e-07, 3.3e-07, 3.6e-07, 4.2e-07, 5.0e-07]
SHORT_FLUXES = [2.1e-08, 2.4e-08, 2.2e-08, 2.9e-08, 3.5e-08]
# Invented integral proton flux in pfu by energy threshold in MeV
PROTON_FLUXES = {1: 8.4, 5: 1.9, 10: 0.42, 30: 0.18, 50: 0.11, 60: 0.09, 100: 0.05, 500: 0.01}


def _xray_row(time: datetime, energy: str, flux: float, contaminated: bool) -> dict[str, Any]:
    """Provides one row of a GOES X-ray file in the layout SWPC serves."""
    return {
        "time_tag": time.strftime(GOES_TIME_FORMAT),
        "satellite": SATELLITE,
        "flux": flux,
        "observed_flux": flux * 1.1,
        "electron_correction": flux * 0.1,
        "electron_contaminaton": contaminated,
        "energy": energy,
    }


def _xray_rows(first_minute: datetime, long_fluxes: list[float], short_fluxes: list[float]) -> list[dict[str, Any]]:
    """Provides the rows of both X-ray channels for consecutive minutes from the first minute on, the short channel contaminated."""
    rows = []
    for minute_number, (long_flux, short_flux) in enumerate(zip(long_fluxes, short_fluxes, strict=True)):
        time = first_minute + minute_number * MINUTE
        rows.append(_xray_row(time, "0.1-0.8nm", long_flux, contaminated=False))
        rows.append(_xray_row(time, "0.05-0.4nm", short_flux, contaminated=True))
    return rows


def _proton_rows(time: datetime) -> list[dict[str, Any]]:
    """Provides the rows of every energy threshold of a GOES integral proton file at one time."""
    return [
        {"time_tag": time.strftime(GOES_TIME_FORMAT), "satellite": SATELLITE, "flux": flux, "energy": f">={threshold} MeV"}
        for threshold, flux in PROTON_FLUXES.items()
    ]


def _kp_row(time: datetime, estimated_kp: float, kp_index: int, kp: str) -> dict[str, Any]:
    """Provides one row of the 1-minute planetary K index file in the layout SWPC serves."""
    return {"time_tag": time.strftime(KP_TIME_FORMAT), "kp_index": kp_index, "estimated_kp": estimated_kp, "kp": kp}


def _client(base_url: str) -> SwpcClient:
    """Provides an SWPC client of a base URL with the fixed clock that repeats failing requests without waiting."""
    return SwpcClient(base_url=base_url, clock=lambda: NOW, retry_backoff_factor=0)


def _pipeline(db: Database, client: SwpcClient) -> SwpcPipeline:
    """Provides the SWPC pipeline of a client on the test database."""
    return SwpcPipeline(timeseries=MongoTimeSeriesRepository(db), coverage_repository=MongoCoverageRepository(db), client=client)


def _ingest(db: Database, httpserver: HTTPServer, datasets: list[str], start: datetime = NOW - timedelta(hours=2)) -> IngestionReport:
    """Runs the SWPC pipeline against the local test server up to the current time and returns its report."""
    return _pipeline(db, _client(httpserver.url_for(SERVER_PATH))).run(datasets, start, NOW)


@pytest.mark.integration
def test_swpc_xray_rows_become_one_document_per_minute(
    db: Database,
    httpserver: HTTPServer,
    stored_timestamps_and_metadata: StoredTimestampsAndMetadata,
    stored_data: StoredData,
) -> None:
    """The two channel rows of each minute become one document with flux and contamination of both channels, the satellite and the X-ray source."""
    httpserver.expect_request(XRAY_6_HOUR_PATH).respond_with_json(_xray_rows(FIRST_MINUTE, LONG_FLUXES, SHORT_FLUXES))

    _ingest(db, httpserver, [XRAY_DATASET])

    expected_times = [FIRST_MINUTE + minute_number * MINUTE for minute_number in range(len(LONG_FLUXES))]
    assert stored_timestamps_and_metadata(XRAY_DATASET) == [(time, {"source": "SWPC/goes-primary-xrays"}) for time in expected_times]
    assert stored_data(XRAY_DATASET, FIRST_MINUTE + MINUTE) == {
        "long_flux": 3.3e-07,
        "short_flux": 2.4e-08,
        "long_electron_contamination": False,
        "short_electron_contamination": True,
        "satellite": SATELLITE,
    }


@pytest.mark.integration
def test_swpc_proton_rows_become_one_field_per_energy_threshold(db: Database, httpserver: HTTPServer, stored_data: StoredData) -> None:
    """The proton rows of one time become one document with a field p_ge{number}mev_flux per energy threshold and the satellite."""
    httpserver.expect_request(PROTON_6_HOUR_PATH).respond_with_json(_proton_rows(FIRST_MINUTE))

    _ingest(db, httpserver, [PROTON_DATASET])

    assert stored_data(PROTON_DATASET, FIRST_MINUTE) == {
        "p_ge1mev_flux": 8.4,
        "p_ge5mev_flux": 1.9,
        "p_ge10mev_flux": 0.42,
        "p_ge30mev_flux": 0.18,
        "p_ge50mev_flux": 0.11,
        "p_ge60mev_flux": 0.09,
        "p_ge100mev_flux": 0.05,
        "p_ge500mev_flux": 0.01,
        "satellite": SATELLITE,
    }


@pytest.mark.integration
def test_swpc_kp_time_without_zone_is_stored_as_utc(
    db: Database, httpserver: HTTPServer, stored_timestamps_and_metadata: StoredTimestampsAndMetadata, stored_data: StoredData
) -> None:
    """A Kp time stamp without zone designator is stored as the same wall time in UTC with estimated_kp and kp_index."""
    httpserver.expect_request(KP_PATH).respond_with_json([_kp_row(FIRST_MINUTE, 2.33, 2, "2P"), _kp_row(FIRST_MINUTE + MINUTE, 2.67, 2, "3M")])

    _ingest(db, httpserver, [KP_DATASET])

    assert [timestamp for timestamp, _ in stored_timestamps_and_metadata(KP_DATASET)] == [FIRST_MINUTE, FIRST_MINUTE + MINUTE]
    assert stored_data(KP_DATASET, FIRST_MINUTE) == {"estimated_kp": 2.33, "kp_index": 2}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("lookback", "expected_path"),
    [
        pytest.param(timedelta(hours=2), "/json/goes/primary/xrays-6-hour.json", id="hours-6-hour-file"),
        pytest.param(timedelta(days=2), "/json/goes/primary/xrays-3-day.json", id="days-3-day-file"),
        pytest.param(timedelta(days=9), "/json/goes/primary/xrays-7-day.json", id="older-than-7-days"),
    ],
)
def test_swpc_requests_shortest_file_reaching_back_to_the_start(
    db: Database, httpserver: HTTPServer, lookback: timedelta, expected_path: str
) -> None:
    """The run requests the shortest rolling file that reaches back to the start, or the 7-day file for an older start."""
    httpserver.expect_request(expected_path).respond_with_json(_xray_rows(FIRST_MINUTE, LONG_FLUXES, SHORT_FLUXES))

    _ingest(db, httpserver, [XRAY_DATASET], start=NOW - lookback)

    assert [request.path for request, _ in httpserver.log] == [expected_path]


@pytest.mark.integration
def test_swpc_rerun_with_shifted_file_inserts_only_the_new_minute(db: Database, httpserver: HTTPServer) -> None:
    """A second run whose file lost its first minute and gained a new one keeps every stored document and inserts only the new minute."""
    httpserver.expect_request(XRAY_6_HOUR_PATH).respond_with_json(_xray_rows(FIRST_MINUTE, LONG_FLUXES, SHORT_FLUXES))
    _ingest(db, httpserver, [XRAY_DATASET])
    ids_before = {document["_id"] for document in db[XRAY_DATASET].find()}
    httpserver.clear()
    shifted_rows = _xray_rows(FIRST_MINUTE + MINUTE, [*LONG_FLUXES[1:], 6.1e-07], [*SHORT_FLUXES[1:], 4.0e-08])
    httpserver.expect_request(XRAY_6_HOUR_PATH).respond_with_json(shifted_rows)

    _ingest(db, httpserver, [XRAY_DATASET])

    ids_after = {document["_id"] for document in db[XRAY_DATASET].find()}
    assert (len(ids_after), ids_before <= ids_after) == (len(LONG_FLUXES) + 1, True)


# Address on which no server listens, so every connection is refused at once
UNREACHABLE_URL = "http://127.0.0.1:1/json/"


@pytest.mark.integration
def test_swpc_unreachable_server_leaves_remaining_datasets_unrequested(db: Database) -> None:
    """After a refused connection the first dataset is a gap of the unreachable source and the next one a gap without request."""
    client = SwpcClient(base_url=UNREACHABLE_URL, clock=lambda: NOW, retry_count=0, retry_backoff_factor=0)

    report = _pipeline(db, client).run([XRAY_DATASET, KP_DATASET], NOW - timedelta(hours=2), NOW)

    assert [gap.reason.split(":")[0] for gap in report.gaps] == ["SourceUnreachableError", "Not requested, the provider was unreachable"]
