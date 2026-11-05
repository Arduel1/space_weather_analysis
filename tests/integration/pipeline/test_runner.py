import json
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from pymongo.database import Database
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from pipeline_service.chunk_status.repository import MongoChunkStatusRepository
from pipeline_service.jobs.runner import CATALOG_RETRY_DELAY, CATALOG_WAIT_LIMIT, JobRunner
from pipeline_service.pipelines.base import Pipeline
from pipeline_service.pipelines.factory import PipelineFactory
from pipeline_service.pipelines.gfz import GfzPipeline
from space_weather_analysis.catalog.models import SourceType, TimeRangeDict
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.clients.gfz import GfzClient
from space_weather_analysis.coverage.repository import MongoCoverageRepository
from space_weather_analysis.jobs.models import Job, JobStatus
from space_weather_analysis.jobs.repository import MongoJobRepository
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.timeseries.models import TimeBucket
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository

INDEX = "Kp"
SERVER_PATH = "/app/json/"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DAY_ONE = datetime(2024, 1, 1, tzinfo=UTC)
DAY_TWO = DAY_ONE + timedelta(days=1)
DAY_THREE = DAY_ONE + timedelta(days=2)
KP_INTERVAL = timedelta(hours=3)
CATALOG_RANGE: TimeRangeDict = {"start_time": "1932-01-01T00:00:00Z", "end_time": "2024-12-31T00:00:00Z"}


class FakeGfzServer:
    """Fake GFZ JSON endpoint serving a constant Kp of 2.0 for every interval, with HTTP 500 for the failing request starts."""

    def __init__(self, url: str) -> None:
        """Starts without failing requests, served at the given base URL."""
        self.url = url
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
def gfz_server(httpserver: HTTPServer) -> FakeGfzServer:
    """Fake GFZ server on the local test HTTP server."""
    fake_server = FakeGfzServer(httpserver.url_for(SERVER_PATH))
    httpserver.expect_request(SERVER_PATH).respond_with_handler(fake_server.handle)
    return fake_server


def _gfz_pipeline(db: Database, gfz_url: str) -> GfzPipeline:
    """Builds the GFZ pipeline with one-day chunks and a client of the fake server without request repetitions."""
    return GfzPipeline(
        timeseries=MongoTimeSeriesRepository(db),
        metadata_repository=MongoMetadataRepository(db),
        coverage_repository=MongoCoverageRepository(db),
        chunk_status=MongoChunkStatusRepository(db),
        client=GfzClient(base_url=gfz_url, retry_count=0),
        chunk_unit=TimeBucket.DAY,
    )


def _run_job(
    db: Database,
    data_dir: pathlib.Path,
    source_type: SourceType,
    datasets: list[str],
    gfz_url: str | None = None,
    start: datetime = DAY_ONE,
    end: datetime = DAY_THREE,
) -> Job:
    """Creates a job, lets the job runner process it and returns the stored job; with a GFZ URL the GFZ pipeline uses that server."""
    repository = MongoJobRepository(db)
    job_id = repository.create(datasets, start, end, source_type=source_type)
    factory = PipelineFactory(db, data_dir)

    def pipeline_for(requested_type: SourceType) -> Pipeline:
        """Builds the GFZ pipeline of the given server and every other pipeline through the factory."""
        if requested_type == SourceType.GFZ_POTSDAM and gfz_url is not None:
            return _gfz_pipeline(db, gfz_url)
        return factory.get_pipeline(requested_type)

    assert JobRunner(db, data_dir, pipeline_for=pipeline_for).process_next_job(source_type)

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

    job = _run_job(db, tmp_path, SourceType.GFZ_POTSDAM, [INDEX], gfz_url=gfz_server.url)

    assert (job.status, sorted((gap.start, gap.stop) for gap in job.missing_chunks)) == (
        expected_status,
        sorted((start, start + timedelta(days=1)) for start in failing_starts),
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("source_type", "datasets"),
    [
        pytest.param(SourceType.GFZ_POTSDAM, ["kp"], id="unknown_gfz_index"),
        pytest.param(SourceType.LOCAL, ["not_a_local_dataset"], id="unknown_local_dataset"),
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

    report = _gfz_pipeline(db, gfz_server.url).run([INDEX], DAY_ONE, DAY_TWO)

    assert [(gap.start, gap.reason.split(":")[0]) for gap in report.gaps] == [(DAY_ONE, "BulkWriteError")]
    assert db[INDEX].count_documents({}) == 0


def _uncatalogued_kp_job(db: Database) -> tuple[MongoJobRepository, Job]:
    """Creates a job for the Kp index without source type while the catalog does not list Kp, and returns it as stored."""
    repository = MongoJobRepository(db)
    job = repository.get(repository.create([INDEX], DAY_ONE, DAY_TWO))
    assert job is not None
    return repository, job


@pytest.mark.integration
def test_job_for_a_dataset_not_yet_in_the_catalog_waits_and_runs_once_the_catalog_lists_it(
    db: Database, tmp_path: pathlib.Path, gfz_server: FakeGfzServer
) -> None:
    """The job goes back to the queue until its retry time and completes after the catalog lists the dataset."""
    repository, job = _uncatalogued_kp_job(db)
    clock_times = [job.created_at]
    runner = JobRunner(db, tmp_path, pipeline_for=lambda source_type: _gfz_pipeline(db, gfz_server.url), clock=lambda: clock_times[-1])

    runner.dispatch_next_job()
    waiting = repository.get(job.id)
    claimed_before_retry = runner.dispatch_next_job()
    MongoCatalogRepository(db).save(
        {"dataset_id": INDEX, "sources": [{"type": SourceType.GFZ_POTSDAM, "source_id": "GFZ", "time_range": CATALOG_RANGE}]}
    )
    clock_times.append(job.created_at + CATALOG_RETRY_DELAY)
    runner.dispatch_next_job()
    runner.process_next_job(SourceType.GFZ_POTSDAM)

    assert waiting is not None
    assert (waiting.status, waiting.retry_at, claimed_before_retry) == (JobStatus.PENDING, job.created_at + CATALOG_RETRY_DELAY, False)
    finished = repository.get(job.id)
    assert finished is not None and finished.status == JobStatus.COMPLETED


@pytest.mark.integration
def test_job_for_a_dataset_the_catalog_never_lists_fails_after_the_wait_limit(db: Database, tmp_path: pathlib.Path) -> None:
    """A job still naming an unknown dataset once the wait limit has passed ends FAILED with the reason."""
    repository, job = _uncatalogued_kp_job(db)
    runner = JobRunner(db, tmp_path, clock=lambda: job.created_at + CATALOG_WAIT_LIMIT)

    runner.dispatch_next_job()

    failed = repository.get(job.id)
    assert failed is not None
    assert (failed.status, INDEX in (failed.error or "")) == (JobStatus.FAILED, True)


@pytest.mark.integration
def test_job_with_datasets_of_two_source_types_is_split_into_one_job_per_lane(db: Database, tmp_path: pathlib.Path) -> None:
    """The job keeps the datasets of its first source type for that lane and a new job takes the others to their lane."""
    catalog = MongoCatalogRepository(db)
    catalog.save({"dataset_id": INDEX, "sources": [{"type": SourceType.GFZ_POTSDAM, "source_id": "GFZ", "time_range": CATALOG_RANGE}]})
    catalog.save({"dataset_id": "cme_catalog", "sources": [{"type": SourceType.LOCAL, "source_id": "cme_catalog", "time_range": CATALOG_RANGE}]})
    repository = MongoJobRepository(db)
    job_id = repository.create([INDEX, "cme_catalog"], DAY_ONE, DAY_TWO)
    runner = JobRunner(db, tmp_path)

    runner.dispatch_next_job()

    gfz_job = repository.claim_next_pending(source_types=[SourceType.GFZ_POTSDAM])
    local_job = repository.claim_next_pending(source_types=[SourceType.LOCAL])
    assert gfz_job is not None and local_job is not None
    assert (gfz_job.id, gfz_job.parameters.datasets, local_job.parameters.datasets) == (job_id, [INDEX], ["cme_catalog"])
    assert repository.claim_next_pending() is None


@pytest.mark.integration
def test_run_until_idle_runs_every_due_job_and_returns(db: Database, tmp_path: pathlib.Path, gfz_server: FakeGfzServer) -> None:
    """Without catalog wait the runner completes the job of a named source type, fails the job of an uncatalogued dataset at once, and returns."""
    repository = MongoJobRepository(db)
    known_id = repository.create([INDEX], DAY_ONE, DAY_TWO, source_type=SourceType.GFZ_POTSDAM)
    uncatalogued_id = repository.create(["not_catalogued"], DAY_ONE, DAY_TWO)
    runner = JobRunner(db, tmp_path, pipeline_for=lambda source_type: _gfz_pipeline(db, gfz_server.url), catalog_wait_limit=timedelta(0))

    runner.run_until_idle()

    statuses = [job.status if job is not None else None for job in (repository.get(known_id), repository.get(uncatalogued_id))]
    assert statuses == [JobStatus.COMPLETED, JobStatus.FAILED]
