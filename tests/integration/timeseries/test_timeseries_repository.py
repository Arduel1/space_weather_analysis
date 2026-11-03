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


def _ids_by_time(db: Database) -> dict[datetime, object]:
    """Maps the time stamp of each stored document to its ID."""
    return {document["timestamp"]: document["_id"] for document in db[COLLECTION].find()}


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
def test_replace_range_writes_only_differences_inside_range(db: Database, repository: MongoTimeSeriesRepository) -> None:
    """Unchanged documents keep their IDs; changed, missing and new ones are written; documents outside the range stay."""
    repository.insert(COLLECTION, _documents(HOURLY_TIMES[:5], 1.0))
    ids_before = _ids_by_time(db)
    range_start, range_stop = HOURLY_TIMES[1], HOURLY_TIMES[5]
    new_documents = [*_documents(HOURLY_TIMES[1:2], 1.0), *_documents(HOURLY_TIMES[2:3], 2.0), *_documents(HOURLY_TIMES[4:6], 1.0)]

    changes = repository.replace_range(COLLECTION, range_start, range_stop, new_documents)

    stored = {document["timestamp"]: document["data"]["value"] for document in db[COLLECTION].find()}
    assert changes == (2, 2)
    assert stored == {HOURLY_TIMES[0]: 1.0, HOURLY_TIMES[1]: 1.0, HOURLY_TIMES[2]: 2.0, HOURLY_TIMES[4]: 1.0, HOURLY_TIMES[5]: 1.0}
    ids_after = _ids_by_time(db)
    assert [ids_after[time] == ids_before[time] for time in (HOURLY_TIMES[0], HOURLY_TIMES[1], HOURLY_TIMES[2], HOURLY_TIMES[4])] == [
        True,
        True,
        False,
        True,
    ]


@pytest.mark.integration
def test_replace_range_with_stored_documents_writes_nothing(repository: MongoTimeSeriesRepository) -> None:
    """Documents equal to the stored ones need no write, also when repeated or timed finer than MongoDB stores."""
    times = [START + timedelta(microseconds=1500), START + timedelta(hours=1), START + timedelta(hours=1)]
    repository.insert(COLLECTION, _documents(times, 1.0))

    changes = repository.replace_range(COLLECTION, START, START + timedelta(hours=2), _documents(times, 1.0))

    assert changes == (0, 0)


@pytest.mark.integration
def test_has_documents_respects_half_open_range(repository: MongoTimeSeriesRepository) -> None:
    """A document at the start counts as inside the range, a document at the stop does not."""
    repository.insert(COLLECTION, _documents([START], 1.0))

    assert repository.has_documents(COLLECTION, START, START + timedelta(hours=1))
    assert not repository.has_documents(COLLECTION, START - timedelta(hours=1), START)
