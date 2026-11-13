import os
import pathlib
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pymongo.database import Database
from pytest_httpserver import HTTPServer

from pipeline_service.pipelines.local import LOCAL_DATASETS, LocalPipeline
from space_weather_analysis.coverage.repository import MongoCoverageRepository
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository

LocalPipelineFor = Callable[[pathlib.Path], LocalPipeline]
WriteDatasetInput = Callable[[pathlib.Path, str], None]

# Dataset IDs, which are also the target collection names.
SUNSPOT_COLLECTION = "silso_sunspot_daily"
CME_COLLECTION = "cme_catalog"

# The synthetic sunspot file a fake provider serves for download
SYNTHETIC_SUNSPOTS = pathlib.Path(__file__).parent / "data" / "synthetic_sunspots.csv"
# Data rows in each synthetic file in data/.
ROWS_PER_INPUT_FILE = 3


@pytest.mark.integration
@pytest.mark.parametrize(
    "collection", [pytest.param(SUNSPOT_COLLECTION, id="sunspot_file_missing"), pytest.param(CME_COLLECTION, id="cme_file_missing")]
)
def test_missing_input_file_is_rejected_and_creates_no_collection(
    db: Database, tmp_path: pathlib.Path, local_pipeline: LocalPipelineFor, collection: str
) -> None:
    """A dataset without input file raises FileNotFoundError, which fails the job, and creates no collection."""
    with pytest.raises(FileNotFoundError):
        local_pipeline(tmp_path).run([collection])

    assert collection not in db.list_collection_names()


@pytest.mark.integration
def test_unknown_dataset_id_is_rejected_and_creates_nothing(db: Database, full_data_dir: pathlib.Path, local_pipeline: LocalPipelineFor) -> None:
    """An unknown dataset ID raises ValueError before any processing and leaves the database unchanged."""
    collections_before = set(db.list_collection_names())

    with pytest.raises(ValueError, match="Unknown local datasets"):
        local_pipeline(full_data_dir).run(["not_a_local_dataset", SUNSPOT_COLLECTION])

    assert set(db.list_collection_names()) == collections_before


@pytest.mark.integration
@pytest.mark.parametrize(
    "collection",
    [pytest.param(SUNSPOT_COLLECTION, id="sunspot_file"), pytest.param(CME_COLLECTION, id="cme_file")],
)
def test_local_dataset_creates_timeseries_collection(
    db: Database, tmp_path: pathlib.Path, write_dataset_input: WriteDatasetInput, local_pipeline: LocalPipelineFor, collection: str
) -> None:
    """Each local dataset gets a time series collection with time field 'timestamp', meta field 'metadata' and granularity hours."""
    write_dataset_input(tmp_path, collection)

    local_pipeline(tmp_path).run([collection])

    collection_info = db.list_collections(filter={"name": collection}).next()
    timeseries_options = collection_info["options"]["timeseries"]
    assert (collection_info["type"], timeseries_options["timeField"], timeseries_options["metaField"], timeseries_options["granularity"]) == (
        "timeseries",
        "timestamp",
        "metadata",
        "hours",
    )


@pytest.mark.integration
def test_local_run_records_coverage_of_stored_documents(db: Database, sunspot_dir: pathlib.Path, local_pipeline: LocalPipelineFor) -> None:
    """After a run the coverage of the dataset spans the three consecutive synthetic sunspot days."""
    local_pipeline(sunspot_dir).run([SUNSPOT_COLLECTION])

    coverage = MongoCoverageRepository(db).get(SUNSPOT_COLLECTION)
    assert coverage is not None
    assert coverage.model_dump(exclude={"updated_at"}) == {
        "dataset_id": SUNSPOT_COLLECTION,
        "collection": SUNSPOT_COLLECTION,
        "first_timestamp": datetime(2001, 3, 14, tzinfo=UTC),
        "last_timestamp": datetime(2001, 3, 16, tzinfo=UTC),
        "document_count": ROWS_PER_INPUT_FILE,
        "intervals": [{"start": datetime(2001, 3, 14, tzinfo=UTC), "end": datetime(2001, 3, 17, tzinfo=UTC)}],
        "bucket": "PT24H",
    }


@pytest.mark.integration
def test_file_without_valid_row_leaves_gap_and_keeps_stored_documents(
    db: Database, sunspot_dir: pathlib.Path, local_pipeline: LocalPipelineFor
) -> None:
    """A file whose rows all lack a valid date is reported as gap over all time, and the documents of the previous run stay."""
    local_pipeline(sunspot_dir).run([SUNSPOT_COLLECTION])
    (sunspot_dir / "SN_d_tot_V2.0.csv").write_text("2001;13;01;2001.900;  10; 1.0;   5;1\n")

    report = local_pipeline(sunspot_dir).run([SUNSPOT_COLLECTION])

    assert ([gap.reason.split(":")[0] for gap in report.gaps], report.chunks_total) == (["DataProcessingError"], 1)
    assert db[SUNSPOT_COLLECTION].count_documents({}) == ROWS_PER_INPUT_FILE


@pytest.mark.integration
def test_rerun_with_one_appended_row_inserts_only_that_row(db: Database, sunspot_dir: pathlib.Path, local_pipeline: LocalPipelineFor) -> None:
    """A second run keeps the unchanged documents with their IDs and inserts only the document of the new row."""
    local_pipeline(sunspot_dir).run([SUNSPOT_COLLECTION])
    ids_before = {document["_id"] for document in db[SUNSPOT_COLLECTION].find()}
    with (sunspot_dir / "SN_d_tot_V2.0.csv").open("a", encoding="utf-8") as file:
        file.write("2001;03;17;2001.207;   42;  3.0;  20;0\n")

    local_pipeline(sunspot_dir).run([SUNSPOT_COLLECTION])

    ids_after = {document["_id"] for document in db[SUNSPOT_COLLECTION].find()}
    assert (len(ids_after), ids_before <= ids_after) == (ROWS_PER_INPUT_FILE + 1, True)


# Curated metadata of the daily sunspot numbers, written into the curated directory of a test
SUNSPOT_CURATED_TOML = """
[dataset.silso_sunspot_daily]
title = "Invented sunspot title"

[[dataset.silso_sunspot_daily.variable]]
name = "sunspot_number"
units = "invented unit"

[[dataset.silso_sunspot_daily.reference]]
url = "https://provider.invalid/page"
retrieved = 2026-10-01
covers = "title, unit of sunspot_number"
"""


@pytest.mark.integration
def test_local_run_saves_metadata_record_with_curated_values(
    db: Database, sunspot_dir: pathlib.Path, local_pipeline: LocalPipelineFor, no_curated_metadata: pathlib.Path
) -> None:
    """A run saves a LOCAL record with one variable per stored column, its title and a variable unit taken from the curated file."""
    (no_curated_metadata / "silso.toml").write_text(SUNSPOT_CURATED_TOML, encoding="utf-8")

    local_pipeline(sunspot_dir).run([SUNSPOT_COLLECTION])

    record = MongoMetadataRepository(db).get(SUNSPOT_COLLECTION)
    assert record is not None
    assert (record.source.type, record.title, {variable.name: variable.units for variable in record.variables}) == (
        "LOCAL",
        "Invented sunspot title",
        {
            "sunspot_number": "invented unit",
            "std_dev": None,
            "num_observations": None,
            "is_provisional": None,
            "decimal_year": None,
        },
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("file_age", "server_status", "expected_requests"),
    [
        pytest.param(None, 200, 1, id="missing-file-downloaded"),
        pytest.param(timedelta(days=2), 500, 1, id="outdated-file-kept-when-download-fails"),
        pytest.param(timedelta(hours=1), 200, 0, id="fresh-file-not-downloaded"),
    ],
)
def test_file_with_download_url_is_fetched_when_missing_or_outdated(
    db: Database, tmp_path: pathlib.Path, httpserver: HTTPServer, file_age: timedelta | None, server_status: int, expected_requests: int
) -> None:
    """A missing or outdated file is fetched before reading, a failed fetch keeps the previous file, and a fresh file is read as it is."""
    content = SYNTHETIC_SUNSPOTS.read_text(encoding="utf-8")
    httpserver.expect_request("/sunspots.csv").respond_with_data(content, status=server_status)
    dataset = replace(LOCAL_DATASETS[SUNSPOT_COLLECTION], url=httpserver.url_for("/sunspots.csv"))
    if file_age is not None:
        stored_file = tmp_path / dataset.file_name
        stored_file.write_text(content, encoding="utf-8")
        modified = time.time() - file_age.total_seconds()
        os.utime(stored_file, (modified, modified))
    pipeline = LocalPipeline(
        timeseries=MongoTimeSeriesRepository(db),
        coverage_repository=MongoCoverageRepository(db),
        metadata_repository=MongoMetadataRepository(db),
        data_dir=tmp_path,
        local_datasets={SUNSPOT_COLLECTION: dataset},
    )

    report = pipeline.run([SUNSPOT_COLLECTION])

    assert (len(httpserver.log), report.gaps, db[SUNSPOT_COLLECTION].count_documents({})) == (expected_requests, [], ROWS_PER_INPUT_FILE)
