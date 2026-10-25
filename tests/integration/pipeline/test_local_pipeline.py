import pathlib
from typing import Callable

import pytest
from pymongo.database import Database

from pipeline_service.pipelines.local import LocalPipeline

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
