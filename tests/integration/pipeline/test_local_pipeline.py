import pathlib
from datetime import datetime, timezone
from typing import Callable

import pytest
from pymongo.database import Database

from pipeline_service.pipelines.local import LocalPipeline
from space_weather_analysis.coverage.repository import MongoCoverageRepository

WriteDatasetInput = Callable[[pathlib.Path, str], None]

# Dataset IDs, which are also the target collection names.
SUNSPOT_COLLECTION = "silso_sunspot_daily"
CME_COLLECTION = "cme_catalog"

# Data rows in each synthetic file in data/.
ROWS_PER_INPUT_FILE = 3


@pytest.mark.integration
@pytest.mark.parametrize(
    ("missing_collection", "loaded_collection"),
    [
        pytest.param(SUNSPOT_COLLECTION, CME_COLLECTION, id="sunspot_file_missing"),
        pytest.param(CME_COLLECTION, SUNSPOT_COLLECTION, id="cme_file_missing"),
    ],
)
def test_missing_input_file_creates_no_collection_without_raising(
    db: Database,
    tmp_path: pathlib.Path,
    write_dataset_input: WriteDatasetInput,
    missing_collection: str,
    loaded_collection: str,
) -> None:
    """A dataset without input file creates no collection; the other dataset of the run is still loaded."""
    write_dataset_input(tmp_path, loaded_collection)

    LocalPipeline(db, tmp_path).run([SUNSPOT_COLLECTION, CME_COLLECTION])

    assert missing_collection not in db.list_collection_names()
    assert db[loaded_collection].count_documents({}) == ROWS_PER_INPUT_FILE


@pytest.mark.integration
def test_unknown_dataset_id_creates_nothing_without_raising(db: Database, full_data_dir: pathlib.Path) -> None:
    """An unknown dataset ID leaves the database unchanged."""
    collections_before = set(db.list_collection_names())

    LocalPipeline(db, full_data_dir).run(["not_a_local_dataset"])

    assert set(db.list_collection_names()) == collections_before


@pytest.mark.integration
@pytest.mark.parametrize(
    "collection",
    [pytest.param(SUNSPOT_COLLECTION, id="sunspot_processor"), pytest.param(CME_COLLECTION, id="cme_processor")],
)
def test_local_dataset_creates_timeseries_collection(
    db: Database, tmp_path: pathlib.Path, write_dataset_input: WriteDatasetInput, collection: str
) -> None:
    """Each local processor creates its collection as time series with time field 'timestamp' and meta field 'metadata'."""
    write_dataset_input(tmp_path, collection)

    LocalPipeline(db, tmp_path).run([collection])

    collection_info = db.list_collections(filter={"name": collection}).next()
    timeseries_options = collection_info["options"]["timeseries"]
    assert (collection_info["type"], timeseries_options["timeField"], timeseries_options["metaField"]) == ("timeseries", "timestamp", "metadata")


@pytest.mark.integration
def test_local_run_records_coverage_of_stored_documents(db: Database, sunspot_dir: pathlib.Path) -> None:
    """After a run the coverage of the dataset spans the three consecutive synthetic sunspot days."""
    LocalPipeline(db, sunspot_dir).run([SUNSPOT_COLLECTION])

    coverage = MongoCoverageRepository(db).get(SUNSPOT_COLLECTION)
    assert coverage is not None
    assert coverage.model_dump(exclude={"updated_at"}) == {
        "dataset_id": SUNSPOT_COLLECTION,
        "collection": SUNSPOT_COLLECTION,
        "first_timestamp": datetime(2001, 3, 14, tzinfo=timezone.utc),
        "last_timestamp": datetime(2001, 3, 16, tzinfo=timezone.utc),
        "document_count": ROWS_PER_INPUT_FILE,
        "intervals": [{"start": datetime(2001, 3, 14, tzinfo=timezone.utc), "end": datetime(2001, 3, 17, tzinfo=timezone.utc)}],
        "bucket": "PT24H",
    }
