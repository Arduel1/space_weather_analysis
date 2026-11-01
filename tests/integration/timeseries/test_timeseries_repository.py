from datetime import UTC, datetime, timedelta

import pytest
from pymongo.database import Database

from space_weather_analysis.timeseries.documents import TimeSeriesDocument
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository

COLLECTION = "synthetic_dataset"
START = datetime(2024, 1, 1, tzinfo=UTC)
# Six hourly time stamps from START on.
HOURLY_TIMES = [START + timedelta(hours=hour) for hour in range(6)]


def _documents(times: list[datetime], value: float) -> list[TimeSeriesDocument]:
    """Builds one time series document per time with the same data value."""
    return [{"timestamp": time, "metadata": {"source": "LOCAL/SYNTHETIC"}, "data": {"value": value}} for time in times]


@pytest.fixture
def repository(db: Database) -> MongoTimeSeriesRepository:
    """Repository with an empty hourly time series collection."""
    repository = MongoTimeSeriesRepository(db)
    repository.ensure_collection(COLLECTION, Granularity.HOURS)
    return repository


@pytest.mark.integration
def test_ensure_collection_creates_time_series_collection_once(db: Database, repository: MongoTimeSeriesRepository) -> None:
    """The collection is a time series collection with the document fields; a second call keeps it."""
    repository.ensure_collection(COLLECTION, Granularity.HOURS)

    options = db[COLLECTION].options()
    assert options["timeseries"]["timeField"] == "timestamp"
    assert options["timeseries"]["metaField"] == "metadata"
    assert options["timeseries"]["granularity"] == "hours"


@pytest.mark.integration
def test_insert_then_count_in_range(repository: MongoTimeSeriesRepository) -> None:
    """Every inserted document is counted, and only the documents inside the half-open range."""
    inserted = repository.insert(COLLECTION, _documents(HOURLY_TIMES, 1.0))

    assert inserted == 6
    assert repository.count(COLLECTION, START, START + timedelta(hours=6)) == 6
    assert repository.count(COLLECTION, START + timedelta(hours=1), START + timedelta(hours=3)) == 2


@pytest.mark.integration
def test_replace_range_replaces_only_documents_inside_range(repository: MongoTimeSeriesRepository) -> None:
    """Documents outside the range keep their values; the range holds only the new documents."""
    repository.insert(COLLECTION, _documents(HOURLY_TIMES, 1.0))
    range_start, range_stop = START + timedelta(hours=2), START + timedelta(hours=4)

    inserted = repository.replace_range(COLLECTION, range_start, range_stop, _documents([range_start], 2.0))

    assert inserted == 1
    assert repository.count(COLLECTION, range_start, range_stop) == 1
    assert repository.count(COLLECTION, range_start, range_stop, {"value": 2.0}) == 1
    assert repository.count(COLLECTION, START, START + timedelta(hours=6), {"value": 1.0}) == 4


@pytest.mark.integration
def test_replace_all_keeps_only_new_documents(repository: MongoTimeSeriesRepository) -> None:
    """Every stored document is replaced, also outside the time span of the new documents."""
    repository.insert(COLLECTION, _documents(HOURLY_TIMES, 1.0))

    inserted = repository.replace_all(COLLECTION, _documents(HOURLY_TIMES[:2], 3.0))

    assert inserted == 2
    assert repository.count(COLLECTION, START, START + timedelta(hours=6)) == 2
    assert repository.count(COLLECTION, START, START + timedelta(hours=6), {"value": 3.0}) == 2


@pytest.mark.integration
def test_has_documents_respects_half_open_range(repository: MongoTimeSeriesRepository) -> None:
    """A document at the start counts as inside the range, a document at the stop does not."""
    repository.insert(COLLECTION, _documents([START], 1.0))

    assert repository.has_documents(COLLECTION, START, START + timedelta(hours=1))
    assert not repository.has_documents(COLLECTION, START - timedelta(hours=1), START)
