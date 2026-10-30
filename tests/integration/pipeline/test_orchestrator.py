import json
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from pymongo.database import Database
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from pipeline_service.orchestrator import PipelineOrchestrator
from pipeline_service.pipelines.gfz import GfzPipeline
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.gfz import GfzClient
from space_weather_analysis.jobs.models import Job, JobStatus
from space_weather_analysis.jobs.repository import MongoJobRepository

INDEX = "Kp"
SERVER_PATH = "/app/json/"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DAY_ONE = datetime(2024, 1, 1, tzinfo=UTC)
DAY_TWO = DAY_ONE + timedelta(days=1)
DAY_THREE = DAY_ONE + timedelta(days=2)
KP_INTERVAL = timedelta(hours=3)


class FakeGfzServer:
    """Fake GFZ JSON endpoint serving a constant Kp of 2.0 for every interval, with HTTP 500 for the failing request starts."""

    def __init__(self) -> None:
        """Starts without failing requests."""
        self.failing_starts: set[datetime] = set()

    def handle(self, request: Request) -> Response:
        """Answers like GFZ with one value per 3-hour interval in [start, end]."""
        start = datetime.strptime(request.args["start"], TIME_FORMAT).replace(tzinfo=UTC)
        end = datetime.strptime(request.args["end"], TIME_FORMAT).replace(tzinfo=UTC)
        if start in self.failing_starts:
            return Response("Internal Server Error", status=500)
        times = [start + interval_number * KP_INTERVAL for interval_number in range(int((end - start) / KP_INTERVAL) + 1)]
        body = {INDEX: [2.0] * len(times), "datetime": [time.strftime(TIME_FORMAT) for time in times], "meta": {}, "status": ["def"] * len(times)}
        return Response(json.dumps(body), content_type="application/json")


@pytest.fixture
def gfz_server(httpserver: HTTPServer, monkeypatch: pytest.MonkeyPatch) -> FakeGfzServer:
    """Fake GFZ server the client is pointed at, without request repetitions and with one-day pipeline chunks."""
    fake_server = FakeGfzServer()
    httpserver.expect_request(SERVER_PATH).respond_with_handler(fake_server.handle)
    monkeypatch.setattr(GfzClient, "BASE_URL", httpserver.url_for(SERVER_PATH))
    monkeypatch.setattr(GfzClient, "RETRY_COUNT", 0)
    monkeypatch.setattr(GfzPipeline, "CHUNK_DURATION", timedelta(days=1))
    return fake_server


def _run_job(
    db: Database, data_dir: pathlib.Path, source_type: SourceType, datasets: list[str], start: datetime = DAY_ONE, end: datetime = DAY_THREE
) -> Job:
    """Creates a job, lets the orchestrator process it and returns the stored job."""
    repository = MongoJobRepository(db)
    job_id = repository.create(datasets, start, end, source_type=source_type)

    assert PipelineOrchestrator(db, data_dir).process_next_job()

    job = repository.get(job_id)
    assert job is not None
    return job


@pytest.mark.integration
@pytest.mark.parametrize(
    ("failing_starts", "expected_status"),
    [
        pytest.param({DAY_ONE}, JobStatus.COMPLETED, id="one_chunk_failed"),
        pytest.param({DAY_ONE, DAY_TWO}, JobStatus.FAILED, id="all_chunks_failed"),
    ],
)
def test_failed_chunks_are_stored_as_missing_and_fail_the_job_only_when_all_fail(
    db: Database, tmp_path: pathlib.Path, gfz_server: FakeGfzServer, failing_starts: set[datetime], expected_status: JobStatus
) -> None:
    """A job keeps its failed chunks as missing chunks; it completes when another chunk was ingested and fails when every chunk failed."""
    gfz_server.failing_starts = failing_starts

    job = _run_job(db, tmp_path, SourceType.GFZ_POTSDAM, [INDEX])

    assert (job.status, sorted((gap.start, gap.stop) for gap in job.missing_chunks)) == (
        expected_status,
        sorted((start, start + timedelta(days=1)) for start in failing_starts),
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("source_type", "datasets"),
    [
        pytest.param(SourceType.GFZ_POTSDAM, ["kp"], id="unknown_gfz_index"),
        pytest.param(SourceType.LOCAL, ["silso_sunspot_daily"], id="missing_local_file"),
    ],
)
def test_invalid_request_fails_the_job_with_the_reason(db: Database, tmp_path: pathlib.Path, source_type: SourceType, datasets: list[str]) -> None:
    """A job the pipeline rejects before ingesting anything ends FAILED with the reason and without missing chunks."""
    job = _run_job(db, tmp_path, source_type, datasets)

    assert (job.status, bool(job.error), job.missing_chunks) == (JobStatus.FAILED, True, [])


@pytest.mark.integration
def test_rejected_write_is_reported_as_gap(db: Database, tmp_path: pathlib.Path, gfz_server: FakeGfzServer) -> None:
    """A chunk whose documents MongoDB rejects becomes a gap with the write error as reason."""
    # A collection whose validator rejects every document makes each insert fail
    db.create_collection(INDEX, validator={"$jsonSchema": {"required": ["field_no_document_has"]}})

    report = GfzPipeline(db, GfzClient(retry_backoff_factor=0)).run([INDEX], DAY_ONE, DAY_TWO)

    assert [(gap.start, gap.reason.split(":")[0]) for gap in report.gaps] == [(DAY_ONE, "BulkWriteError")]
    assert db[INDEX].count_documents({}) == 0
