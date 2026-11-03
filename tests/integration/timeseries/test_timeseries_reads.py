from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from pymongo.database import Database

from space_weather_analysis.timeseries.documents import TIME_FIELD, frame_to_documents
from space_weather_analysis.timeseries.models import Granularity, TimeBucket
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository

COLLECTION = "synthetic_reads"
SOURCE = "LOCAL/SYNTHETIC"
START = datetime(2024, 1, 1, tzinfo=UTC)


@pytest.fixture
def repository(db: Database) -> MongoTimeSeriesRepository:
    """Repository with an hourly time series collection holding four hourly documents from START on; "flag" is missing at the third hour."""
    repository = MongoTimeSeriesRepository(db)
    repository.ensure_collection(COLLECTION, Granularity.HOURS)
    frame = pd.DataFrame(
        {"value": [1.0, 2.0, 3.0, 4.0], "flag": [10.0, 20.0, np.nan, 40.0], "label": ["a", "b", "c", "d"]},
        index=pd.date_range(START, periods=4, freq="h"),
    )
    repository.insert(COLLECTION, frame_to_documents(frame, SOURCE))
    return repository


@pytest.mark.integration
def test_read_range_projects_fields_in_half_open_range(repository: MongoTimeSeriesRepository) -> None:
    """Only the requested fields of the documents in [start, stop) are read, sorted by time with an aware UTC index."""
    frame = repository.read_range(COLLECTION, START + timedelta(hours=1), START + timedelta(hours=3), fields=["value"])

    expected_index = pd.DatetimeIndex([START + timedelta(hours=1), START + timedelta(hours=2)], name=TIME_FIELD)
    pd.testing.assert_frame_equal(frame, pd.DataFrame({"value": [2.0, 3.0]}, index=expected_index))
    assert isinstance(frame.index, pd.DatetimeIndex) and str(frame.index.tz) == "UTC"


@pytest.mark.integration
def test_read_range_reads_all_fields_and_marks_missing_field_as_nan(repository: MongoTimeSeriesRepository) -> None:
    """Without fields every data field is read; a field missing in a document is NaN."""
    frame = repository.read_range(COLLECTION, START, START + timedelta(hours=4))

    assert sorted(frame.columns) == ["flag", "label", "value"]
    assert frame["value"].tolist() == [1.0, 2.0, 3.0, 4.0]
    assert frame["flag"].isna().tolist() == [False, False, True, False]


@pytest.mark.integration
def test_read_range_without_matches_returns_empty_frame_with_requested_columns(repository: MongoTimeSeriesRepository) -> None:
    """A range without documents gives an empty frame with the requested columns and an aware UTC index."""
    frame = repository.read_range(COLLECTION, START - timedelta(days=1), START, fields=["value", "flag"])

    assert frame.empty
    assert list(frame.columns) == ["value", "flag"]
    assert isinstance(frame.index, pd.DatetimeIndex) and str(frame.index.tz) == "UTC"


@pytest.mark.integration
def test_read_latest_returns_newest_document_and_none_for_empty_collection(db: Database, repository: MongoTimeSeriesRepository) -> None:
    """The newest document is returned with the requested fields; an empty collection gives None."""
    latest = repository.read_latest(COLLECTION, fields=["value"])

    assert latest == (START + timedelta(hours=3), {"value": 4.0})
    repository.ensure_collection("empty_dataset", Granularity.HOURS)
    assert repository.read_latest("empty_dataset") is None


@pytest.mark.integration
def test_read_aggregated_monthly_means_and_counts_across_month_boundary(db: Database) -> None:
    """Documents are grouped into calendar months in UTC; a field missing in a document is left out of its mean but counted."""
    repository = MongoTimeSeriesRepository(db)
    repository.ensure_collection(COLLECTION, Granularity.HOURS)
    times = pd.DatetimeIndex(["2024-01-31T22:00", "2024-01-31T23:00", "2024-02-01T00:00", "2024-02-01T01:00", "2024-02-01T02:00"], tz="UTC")
    frame = pd.DataFrame({"value": [1.0, 3.0, 10.0, 20.0, 30.0], "flag": [5.0, np.nan, 1.0, 2.0, 3.0]}, index=times)
    repository.insert(COLLECTION, frame_to_documents(frame, SOURCE))

    aggregated = repository.read_aggregated(
        COLLECTION, datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC), ["value", "flag"], TimeBucket.MONTH
    )

    expected_index = pd.DatetimeIndex([datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC)], name=TIME_FIELD)
    expected = pd.DataFrame({"value": [2.0, 20.0], "flag": [5.0, 2.0], "count": [2, 3]}, index=expected_index)
    pd.testing.assert_frame_equal(aggregated, expected)
