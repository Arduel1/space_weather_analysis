import pathlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.database import Database

from pipeline_service.pipelines.local import LocalPipeline

StoredData = Callable[[str, datetime], dict[str, Any]]
DataDirWithExtraRow = Callable[[str, str], pathlib.Path]
StoredTimestampsAndMetadata = Callable[[str], list[tuple[datetime, dict[str, Any]]]]
LocalPipelineFor = Callable[[pathlib.Path], LocalPipeline]
WriteDatasetInput = Callable[[pathlib.Path, str], None]

SUNSPOT_COLLECTION = "silso_sunspot_daily"
SOURCE_METADATA = {"source": "LOCAL/SILSO_V2.0"}

# Days in data/synthetic_sunspots.csv (invented values, SILSO layout); -1 means no number, 0 is a valid number.
REGULAR_DAY = datetime(2001, 3, 14, tzinfo=UTC)
MISSING_DAY = datetime(2001, 3, 15, tzinfo=UTC)
SPOTLESS_DAY = datetime(2001, 3, 16, tzinfo=UTC)
STORED_DAYS = [REGULAR_DAY, MISSING_DAY, SPOTLESS_DAY]

# A valid row appended to the synthetic file.
EXTRA_DAY_ROW = "2001;03;17;2001.207;   95;  9.0;  25;0"


@pytest.mark.integration
def test_sunspot_ingestion_stores_one_document_per_day(
    db: Database, sunspot_dir: pathlib.Path, local_pipeline: LocalPipelineFor, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """Each day yields one document at its UTC midnight with the SILSO file version as source."""
    local_pipeline(sunspot_dir).run([SUNSPOT_COLLECTION])

    assert stored_timestamps_and_metadata(SUNSPOT_COLLECTION) == [(day, SOURCE_METADATA) for day in STORED_DAYS]


@pytest.mark.integration
def test_sunspot_documents_hold_values_with_their_types_and_omit_missing_ones(
    db: Database, sunspot_dir: pathlib.Path, local_pipeline: LocalPipelineFor, stored_data: StoredData
) -> None:
    """Counts are stored as integers and the flag as boolean; a day without a sunspot number has no measurement fields."""
    local_pipeline(sunspot_dir).run([SUNSPOT_COLLECTION])

    stored = {day: stored_data(SUNSPOT_COLLECTION, day) for day in STORED_DAYS}
    assert stored == {
        REGULAR_DAY: pytest.approx(
            {"sunspot_number": 123, "std_dev": 11.5, "num_observations": 27, "is_provisional": False, "decimal_year": 2001.199}
        ),
        MISSING_DAY: pytest.approx({"is_provisional": False, "decimal_year": 2001.201}),
        SPOTLESS_DAY: pytest.approx({"sunspot_number": 0, "std_dev": 0.0, "num_observations": 25, "is_provisional": True, "decimal_year": 2001.204}),
    }
    value_types = {(type(data["sunspot_number"]), type(data["is_provisional"])) for data in (stored[REGULAR_DAY], stored[SPOTLESS_DAY])}
    assert value_types == {(int, bool)}


@pytest.mark.integration
def test_repeated_sunspot_ingestion_replaces_stored_documents(
    db: Database,
    tmp_path: pathlib.Path,
    data_dir_with_extra_row: DataDirWithExtraRow,
    write_dataset_input: WriteDatasetInput,
    local_pipeline: LocalPipelineFor,
    stored_timestamps_and_metadata: StoredTimestampsAndMetadata,
) -> None:
    """A second run stores exactly the days of the current file, so a day the file no longer holds is removed and none is duplicated."""
    local_pipeline(data_dir_with_extra_row(SUNSPOT_COLLECTION, EXTRA_DAY_ROW)).run([SUNSPOT_COLLECTION])
    write_dataset_input(tmp_path, SUNSPOT_COLLECTION)

    local_pipeline(tmp_path).run([SUNSPOT_COLLECTION])

    assert [day for day, _ in stored_timestamps_and_metadata(SUNSPOT_COLLECTION)] == STORED_DAYS
