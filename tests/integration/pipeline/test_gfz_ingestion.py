import json
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pytest
from pymongo.database import Database
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from pipeline_service.pipelines.gfz import GfzPipeline
from space_weather_analysis.clients.gfz import GfzClient
from space_weather_analysis.metadata.repository import MongoMetadataRepository

StoredData = Callable[[str, datetime], Dict[str, Any]]
StoredTimestampsAndMetadata = Callable[[str], List[Tuple[datetime, Dict[str, Any]]]]

INDEX = "Kp"
SOURCE_METADATA = {"source": "GFZ_POTSDAM/Kp"}
SERVER_PATH = "/app/json/"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Meta object as GFZ sends it with every response.
RESPONSE_META = {"license": "CC BY 4.0", "source": "GFZ Potsdam"}

DAY_ONE = datetime(2024, 1, 1, tzinfo=timezone.utc)
DAY_TWO = DAY_ONE + timedelta(days=1)
DAY_THREE = DAY_ONE + timedelta(days=2)
KP_INTERVAL = timedelta(hours=3)

# Invented Kp values for the eight 3-hour intervals of a day.
KP_DAY_ONE = [0.333, 0.667, 1.0, 1.333, 2.0, 2.333, 3.0, 3.667]
KP_DAY_TWO = [4.0, 4.333, 5.0, 5.667, 6.0, 4.667, 3.333, 2.667]
KP_DAY_TWO_REVISED = [4.333, 4.667, 5.333, 6.0, 6.333, 5.0, 3.667, 3.0]

# Value of one interval as served by the fake server: Kp (raw, may be -1 or None) and status.
ServedValue = Tuple[Any, str]


class FakeGfzServer:
    """Fake GFZ JSON endpoint answering from values and failures that a test may change between runs."""

    def __init__(self) -> None:
        """Starts without values and without failing requests."""
        self.values: Dict[datetime, ServedValue] = {}
        self.failing_starts: Set[datetime] = set()

    def set_day(self, day: datetime, kp_values: List[Any], status: str) -> None:
        """Serves one value per 3-hour interval of a day, starting at midnight, all with the same status."""
        for interval_number, kp_value in enumerate(kp_values):
            self.values[day + interval_number * KP_INTERVAL] = (kp_value, status)

    def handle(self, request: Request) -> Response:
        """Answers like GFZ with every value whose time lies in [start, end], HTTP 500 for a failing start."""
        start = _parse_time(request.args["start"])
        end = _parse_time(request.args["end"])
        if request.args["index"] != INDEX or start in self.failing_starts:
            return Response("<html>Internal Server Error</html>", status=500, content_type="text/html")

        times = sorted(time for time in self.values if start <= time <= end)
        body = {
            INDEX: [self.values[time][0] for time in times],
            "datetime": [time.strftime(TIME_FORMAT) for time in times],
            "meta": RESPONSE_META,
            "status": [self.values[time][1] for time in times],
        }
        return Response(json.dumps(body), content_type="application/json")


def _parse_time(text: str) -> datetime:
    """Parses a GFZ time parameter into an aware UTC datetime."""
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=timezone.utc)


@pytest.fixture
def gfz_server(httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch) -> FakeGfzServer:
    """Fake GFZ server the client is pointed at, with one-day pipeline chunks."""
    fake_server = FakeGfzServer()
    httpserver.expect_request(SERVER_PATH).respond_with_handler(fake_server.handle)
    monkeypatch.setattr(GfzClient, "BASE_URL", httpserver.url_for(SERVER_PATH))
    monkeypatch.setattr(GfzPipeline, "CHUNK_DURATION", timedelta(days=1))
    return fake_server


def _ingest(db: Database, tmp_path: pathlib.Path, start: datetime = DAY_ONE, end: datetime = DAY_TWO) -> None:
    """Runs the GFZ pipeline for the Kp index."""
    GfzPipeline(db, tmp_path).run([INDEX], start, end)


def _requested_starts(httpserver: HTTPServer) -> List[str]:
    """Returns the start parameter of every request the fake server received, in order."""
    return [request.args["start"] for request, _ in httpserver.log]


def _naive(time: datetime) -> datetime:
    """Returns an aware UTC datetime as the naive UTC datetime pymongo reads back."""
    return time.replace(tzinfo=None)


def _stored_kp_and_status(db: Database, day: datetime) -> List[Tuple[Optional[float], Optional[str]]]:
    """Returns Kp and status of every stored document of a day, sorted by time."""
    day_filter = {"timestamp": {"$gte": day, "$lt": day + timedelta(days=1)}}
    return [(doc["data"].get(INDEX), doc["data"].get("status")) for doc in db[INDEX].find(day_filter).sort("timestamp", 1)]


@pytest.mark.integration
def test_gfz_ingestion_stores_one_document_per_value_at_its_interval_start(
    db: Database,
    tmp_path: pathlib.Path,
    gfz_server: FakeGfzServer,
    stored_timestamps_and_metadata: StoredTimestampsAndMetadata,
    stored_data: StoredData,
) -> None:
    """Each value yields one document at its interval start (naive UTC) with the Kp float, its status and GFZ as source."""
    gfz_server.set_day(DAY_ONE, KP_DAY_ONE, "def")

    _ingest(db, tmp_path)

    expected_times = [_naive(DAY_ONE + interval_number * KP_INTERVAL) for interval_number in range(8)]
    assert stored_timestamps_and_metadata(INDEX) == [(time, SOURCE_METADATA) for time in expected_times]
    data = stored_data(INDEX, _naive(DAY_ONE + KP_INTERVAL))
    assert data == pytest.approx({"Kp": 0.667, "status": "def"})
    assert isinstance(data["Kp"], float)


@pytest.mark.integration
def test_gfz_missing_kp_omits_field_and_keeps_record(
    db: Database, tmp_path: pathlib.Path, gfz_server: FakeGfzServer, stored_data: StoredData
) -> None:
    """A Kp of -1 or null is omitted while the document and its status are kept."""
    gfz_server.values[DAY_ONE] = (1.0, "def")
    gfz_server.values[DAY_ONE + KP_INTERVAL] = (-1, "pre")
    gfz_server.values[DAY_ONE + 2 * KP_INTERVAL] = (None, "def")

    _ingest(db, tmp_path)

    assert stored_data(INDEX, _naive(DAY_ONE + KP_INTERVAL)) == {"status": "pre"}
    assert stored_data(INDEX, _naive(DAY_ONE + 2 * KP_INTERVAL)) == {"status": "def"}


@pytest.mark.integration
def test_gfz_chunk_boundary_value_stored_once(db: Database, tmp_path: pathlib.Path, gfz_server: FakeGfzServer, httpserver: HTTPServer) -> None:
    """A run over two chunks stores the value at the chunk boundary exactly once, although both requests include it."""
    gfz_server.set_day(DAY_ONE, KP_DAY_ONE, "def")
    gfz_server.set_day(DAY_TWO, KP_DAY_TWO, "def")

    _ingest(db, tmp_path, end=DAY_THREE)

    assert len(httpserver.log) == 2
    assert db[INDEX].count_documents({}) == 16
    assert db[INDEX].count_documents({"timestamp": DAY_TWO}) == 1


@pytest.mark.integration
def test_gfz_second_run_skips_complete_chunk_off_the_kp_grid(
    db: Database, tmp_path: pathlib.Path, gfz_server: FakeGfzServer, httpserver: HTTPServer
) -> None:
    """A complete definitive chunk whose boundaries lie between 3-hour interval starts is skipped by a second run."""
    gfz_server.set_day(DAY_ONE, KP_DAY_ONE, "def")
    gfz_server.set_day(DAY_TWO, KP_DAY_TWO, "def")
    run_start = DAY_ONE + timedelta(hours=1)
    run_end = DAY_TWO + timedelta(hours=1)
    _ingest(db, tmp_path, start=run_start, end=run_end)
    httpserver.clear_log()

    _ingest(db, tmp_path, start=run_start, end=run_end)

    assert len(httpserver.log) == 0
    # Interval starts 03:00 to 21:00 of day one and 00:00 of day two.
    assert db[INDEX].count_documents({}) == 8


@pytest.mark.integration
def test_gfz_second_run_replaces_preliminary_values(db: Database, tmp_path: pathlib.Path, gfz_server: FakeGfzServer, httpserver: HTTPServer) -> None:
    """A second run skips the complete definitive chunk, refetches only the chunk with preliminary values and replaces them without duplicates."""
    gfz_server.set_day(DAY_ONE, KP_DAY_ONE, "def")
    gfz_server.set_day(DAY_TWO, KP_DAY_TWO, "pre")
    _ingest(db, tmp_path, end=DAY_THREE)
    gfz_server.set_day(DAY_TWO, KP_DAY_TWO_REVISED, "def")
    httpserver.clear_log()

    _ingest(db, tmp_path, end=DAY_THREE)

    assert _requested_starts(httpserver) == [DAY_TWO.strftime(TIME_FORMAT)]
    assert db[INDEX].count_documents({}) == 16
    assert _stored_kp_and_status(db, DAY_TWO) == pytest.approx([(kp_value, "def") for kp_value in KP_DAY_TWO_REVISED])


@pytest.mark.integration
def test_gfz_second_run_completes_incomplete_chunk(db: Database, tmp_path: pathlib.Path, gfz_server: FakeGfzServer) -> None:
    """A chunk of which the first run received only part is completed by a second run, without duplicates."""
    gfz_server.set_day(DAY_ONE, KP_DAY_ONE[:4], "def")
    _ingest(db, tmp_path)
    gfz_server.set_day(DAY_ONE, KP_DAY_ONE, "def")

    _ingest(db, tmp_path)

    assert _stored_kp_and_status(db, DAY_ONE) == pytest.approx([(kp_value, "def") for kp_value in KP_DAY_ONE])


@pytest.mark.integration
def test_gfz_failed_chunk_keeps_stored_documents_and_next_chunk_runs(db: Database, tmp_path: pathlib.Path, gfz_server: FakeGfzServer) -> None:
    """A chunk whose request fails with HTTP 500 keeps its stored documents and does not stop the next chunk."""
    gfz_server.set_day(DAY_ONE, KP_DAY_ONE, "pre")
    _ingest(db, tmp_path)
    gfz_server.failing_starts.add(DAY_ONE)
    gfz_server.set_day(DAY_TWO, KP_DAY_TWO, "def")

    _ingest(db, tmp_path, end=DAY_THREE)

    assert _stored_kp_and_status(db, DAY_ONE) == pytest.approx([(kp_value, "pre") for kp_value in KP_DAY_ONE])
    assert _stored_kp_and_status(db, DAY_TWO) == pytest.approx([(kp_value, "def") for kp_value in KP_DAY_TWO])


@pytest.mark.integration
def test_gfz_unknown_index_sends_no_request(db: Database, tmp_path: pathlib.Path, gfz_server: FakeGfzServer, httpserver: HTTPServer) -> None:
    """An index name the GFZ service does not offer is rejected without a request and stores nothing."""
    GfzPipeline(db, tmp_path).run(["kp"], DAY_ONE, DAY_TWO)

    assert httpserver.log == []
    assert db["kp"].count_documents({}) == 0


@pytest.mark.integration
def test_gfz_metadata_records_time_stamp_location_and_license(db: Database, tmp_path: pathlib.Path, gfz_server: FakeGfzServer) -> None:
    """The record holds 'begin' as time stamp location, the Kp cadence, provider and license of the response meta, and the Kp and status variables."""
    gfz_server.set_day(DAY_ONE, KP_DAY_ONE, "def")

    _ingest(db, tmp_path)

    record = MongoMetadataRepository(db).get(INDEX)
    assert record is not None
    assert (record.time.timestamp_location, record.time.cadence) == ("begin", "PT3H")
    assert (record.source.type, record.source.provider, record.citation.license) == ("GFZ_POTSDAM", "GFZ Potsdam", "CC BY 4.0")
    assert [variable.name for variable in record.variables] == ["Kp", "status"]
    status = record.variables[1]
    assert status.flag_meanings == {"def": "definitive", "pre": "preliminary"}
    assert set(status.field_sources.values()) == {"curated"}
    assert [source.kind for source in record.provenance] == ["api", "curated"]
