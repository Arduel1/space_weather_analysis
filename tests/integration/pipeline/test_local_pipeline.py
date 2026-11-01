import pathlib
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pymongo.database import Database

from pipeline_service.pipelines.local import LocalPipeline
from space_weather_analysis.coverage.repository import MongoCoverageRepository

LocalPipelineFor = Callable[[pathlib.Path], LocalPipeline]
WriteDatasetInput = Callable[[pathlib.Path, str], None]

# Dataset IDs, which are also the target collection names.
SUNSPOT_COLLECTION = "silso_sunspot_daily"
CME_COLLECTION = "cme_catalog"

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
