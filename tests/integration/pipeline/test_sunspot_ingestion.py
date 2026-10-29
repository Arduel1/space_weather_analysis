import pathlib
from datetime import datetime
from typing import Any, Callable, Dict, List, Tuple

import pytest
from pymongo.database import Database

from pipeline_service.pipelines.local import LocalPipeline

StoredData = Callable[[str, datetime], Dict[str, Any]]
DataDirWithExtraRow = Callable[[str, str], pathlib.Path]
StoredTimestampsAndMetadata = Callable[[str], List[Tuple[datetime, Dict[str, Any]]]]

SUNSPOT_COLLECTION = "silso_sunspot_daily"
SOURCE_METADATA = {"source": "SILSO_V2.0"}

# Days in data/synthetic_sunspots.csv (invented values, SILSO layout); -1 means no number, 0 is a valid number.
REGULAR_DAY = datetime(2001, 3, 14)
MISSING_DAY = datetime(2001, 3, 15)
SPOTLESS_DAY = datetime(2001, 3, 16)
STORED_DAYS = [REGULAR_DAY, MISSING_DAY, SPOTLESS_DAY]

MEASUREMENT_FIELDS = ["sunspot_number", "std_dev", "num_observations", "decimal_year"]
# The expected flag and the bare column name, which must not be stored; the indicator is 1 definitive, 0 provisional.
INDICATOR_FIELDS = ["is_provisional", "indicator"]

# Rows appended to the synthetic file for the invalid input cases.
NON_NUMERIC_SUNSPOT_ROW = "2001;03;17;2001.207;  abc;  9.4;  22;0"
NON_NUMERIC_SUNSPOT_DAY = datetime(2001, 3, 17)
INVALID_MONTH_ROW = "2001;13;17;2001.999;   95;  9.0;  25;0"


@pytest.mark.integration
def test_sunspot_ingestion_stores_one_document_per_row_at_its_date(
    db: Database, sunspot_dir: pathlib.Path, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """Each row yields one document at its date, midnight naive UTC, with SILSO as source."""
    LocalPipeline(db, sunspot_dir).run([SUNSPOT_COLLECTION])

    # pymongo returns stored datetimes as naive UTC, so the expected times carry no time zone.
    assert stored_timestamps_and_metadata(SUNSPOT_COLLECTION) == [(day, SOURCE_METADATA) for day in STORED_DAYS]


@pytest.mark.integration
def test_sunspot_measurements_stored_as_given(db: Database, sunspot_dir: pathlib.Path, stored_data: StoredData) -> None:
    """The measurement columns are stored unchanged, counts as integers, including a sunspot number of 0."""
    LocalPipeline(db, sunspot_dir).run([SUNSPOT_COLLECTION])

    stored = {
        day: {field: value for field, value in stored_data(SUNSPOT_COLLECTION, day).items() if field in MEASUREMENT_FIELDS}
        for day in (REGULAR_DAY, SPOTLESS_DAY)
    }
    assert stored == {
        REGULAR_DAY: pytest.approx({"sunspot_number": 123, "std_dev": 11.5, "num_observations": 27, "decimal_year": 2001.199}),
        SPOTLESS_DAY: pytest.approx({"sunspot_number": 0, "std_dev": 0.0, "num_observations": 25, "decimal_year": 2001.204}),
    }
    assert all(isinstance(fields[count], int) for fields in stored.values() for count in ("sunspot_number", "num_observations"))


@pytest.mark.integration
def test_sunspot_is_provisional_set_from_indicator(db: Database, sunspot_dir: pathlib.Path, stored_data: StoredData) -> None:
    """Every document has is_provisional (true for indicator 0) and no bare indicator field."""
    LocalPipeline(db, sunspot_dir).run([SUNSPOT_COLLECTION])

    stored = {
        day: {field: value for field, value in stored_data(SUNSPOT_COLLECTION, day).items() if field in INDICATOR_FIELDS} for day in STORED_DAYS
    }
    assert stored == {
        REGULAR_DAY: {"is_provisional": False},
        MISSING_DAY: {"is_provisional": False},
        SPOTLESS_DAY: {"is_provisional": True},
    }
    assert all(isinstance(flags["is_provisional"], bool) for flags in stored.values())


@pytest.mark.integration
def test_sunspot_number_absent_when_value_is_minus_one(db: Database, sunspot_dir: pathlib.Path, stored_data: StoredData) -> None:
    """A sunspot number of -1 omits the number, std_dev and num_observations, while decimal year and indicator flag are kept."""
    LocalPipeline(db, sunspot_dir).run([SUNSPOT_COLLECTION])

    assert stored_data(SUNSPOT_COLLECTION, MISSING_DAY) == pytest.approx({"decimal_year": 2001.201, "is_provisional": False})


@pytest.mark.integration
def test_sunspot_non_numeric_value_affects_only_its_own_field(
    db: Database, data_dir_with_extra_row: DataDirWithExtraRow, stored_data: StoredData
) -> None:
    """A non-numeric sunspot number omits only that field; other rows keep numeric values."""
    data_dir = data_dir_with_extra_row(SUNSPOT_COLLECTION, NON_NUMERIC_SUNSPOT_ROW)

    LocalPipeline(db, data_dir).run([SUNSPOT_COLLECTION])

    data = stored_data(SUNSPOT_COLLECTION, NON_NUMERIC_SUNSPOT_DAY)
    assert data == pytest.approx({"std_dev": 9.4, "num_observations": 22, "decimal_year": 2001.207, "is_provisional": True})
    other_number = stored_data(SUNSPOT_COLLECTION, REGULAR_DAY)["sunspot_number"]
    assert isinstance(other_number, (int, float)) and other_number == 123


@pytest.mark.integration
def test_sunspot_row_skipped_when_date_is_invalid(
    db: Database, data_dir_with_extra_row: DataDirWithExtraRow, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """A row with an invalid calendar date is skipped; the other rows are stored."""
    data_dir = data_dir_with_extra_row(SUNSPOT_COLLECTION, INVALID_MONTH_ROW)

    LocalPipeline(db, data_dir).run([SUNSPOT_COLLECTION])

    assert [timestamp for timestamp, _ in stored_timestamps_and_metadata(SUNSPOT_COLLECTION)] == STORED_DAYS


@pytest.mark.integration
def test_repeated_sunspot_ingestion_does_not_duplicate_documents(db: Database, sunspot_dir: pathlib.Path) -> None:
    """Ingesting the unchanged file twice keeps one document per row."""
    LocalPipeline(db, sunspot_dir).run([SUNSPOT_COLLECTION])
    LocalPipeline(db, sunspot_dir).run([SUNSPOT_COLLECTION])

    assert db[SUNSPOT_COLLECTION].count_documents({}) == len(STORED_DAYS)
