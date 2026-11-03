from datetime import UTC, datetime, timedelta

import pytest
from pymongo.database import Database

from space_weather_analysis.coverage.models import CoverageInterval, StoredCoverage
from space_weather_analysis.coverage.repository import MongoCoverageRepository

DATASET_ID = "synthetic/dataset"
COLLECTION = "synthetic_dataset"

# Sparse documents on 2024-01-01, 2024-01-02 and 2024-01-04 (about one per day); 2024-01-03 holds none.
STORED_TIMES = [
    datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
    datetime(2024, 1, 1, 23, 59, tzinfo=UTC),
    datetime(2024, 1, 2, 12, 0, tzinfo=UTC),
    datetime(2024, 1, 4, 6, 30, tzinfo=UTC),
]
START = datetime(2024, 1, 1, tzinfo=UTC)
# Ten-minute records for six hours with no record between 02:00 and 04:00.
TEN_MINUTE_TIMES = [START + timedelta(minutes=10 * step) for step in range(36) if not 12 <= step < 24]
# Three-hourly records for one day without any gap, like the Kp index.
THREE_HOURLY_TIMES = [START + timedelta(hours=3 * step) for step in range(8)]


def _interval(start: datetime, end: datetime) -> CoverageInterval:
    """Builds a coverage interval."""
    return CoverageInterval(start=start, end=end)


def _store_documents(db: Database, times: list[datetime] = STORED_TIMES) -> None:
    """Stores one document per time in a time series collection like the pipelines create it."""
    db.create_collection(COLLECTION, timeseries={"timeField": "timestamp", "metaField": "metadata"})
    db[COLLECTION].insert_many([{"timestamp": time, "metadata": {"source": "synthetic"}, "data": {"value": 1.0}} for time in times])


@pytest.mark.integration
@pytest.mark.parametrize(
    ("times", "expected_bucket", "expected_intervals"),
    [
        pytest.param(
            STORED_TIMES,
            "PT24H",
            [_interval(START, START + timedelta(days=2)), _interval(START + timedelta(days=3), START + timedelta(days=4))],
            id="daily-spacing-split-at-empty-day",
        ),
        pytest.param(
            TEN_MINUTE_TIMES,
            "PT1H",
            [_interval(START, START + timedelta(hours=2)), _interval(START + timedelta(hours=4), START + timedelta(hours=6))],
            id="sub-hourly-spacing-split-at-two-hour-gap",
        ),
        pytest.param(THREE_HOURLY_TIMES, "PT3H", [_interval(START, START + timedelta(days=1))], id="three-hourly-spacing-without-gap"),
    ],
)
def test_compute_merges_buckets_of_the_spacing_into_intervals(
    db: Database, times: list[datetime], expected_bucket: str, expected_intervals: list[CoverageInterval]
) -> None:
    """Buckets as fine as the spacing allows, at least one hour, merge into intervals split only at real gaps."""
    _store_documents(db, times)

    coverage = MongoCoverageRepository(db).compute(DATASET_ID, COLLECTION)

    assert coverage == StoredCoverage(
        dataset_id=DATASET_ID,
        collection=COLLECTION,
        first_timestamp=times[0],
        last_timestamp=times[-1],
        document_count=len(times),
        intervals=expected_intervals,
        bucket=expected_bucket,
        updated_at=coverage.updated_at,
    )


@pytest.mark.integration
@pytest.mark.parametrize("create_collection", [pytest.param(True, id="empty_collection"), pytest.param(False, id="missing_collection")])
def test_compute_without_documents_has_no_intervals(db: Database, create_collection: bool) -> None:
    """An empty or missing collection yields a document count of 0, no times and no intervals."""
    if create_collection:
        db.create_collection(COLLECTION, timeseries={"timeField": "timestamp", "metaField": "metadata"})

    coverage = MongoCoverageRepository(db).compute(DATASET_ID, COLLECTION)

    assert coverage == StoredCoverage(
        dataset_id=DATASET_ID, collection=COLLECTION, document_count=0, intervals=[], bucket="PT1H", updated_at=coverage.updated_at
    )


@pytest.mark.integration
def test_refreshed_coverage_reads_back_in_utc(db: Database) -> None:
    """A refreshed coverage is saved and reads back unchanged, with every time in UTC."""
    _store_documents(db)
    repository = MongoCoverageRepository(db)

    refreshed = repository.refresh(DATASET_ID, COLLECTION)

    stored = repository.get(DATASET_ID)
    assert stored is not None
    # MongoDB stores times with millisecond precision, so the computation time is compared at that precision.
    assert stored == refreshed.model_copy(
        update={"updated_at": refreshed.updated_at.replace(microsecond=refreshed.updated_at.microsecond // 1000 * 1000)}
    )
    stored_times = [stored.first_timestamp, stored.last_timestamp, stored.updated_at, *[interval.start for interval in stored.intervals]]
    assert all(time is not None and time.tzinfo == UTC for time in stored_times)


@pytest.mark.integration
def test_list_all_reads_every_saved_coverage_sorted_by_dataset_id(db: Database) -> None:
    """Every saved coverage reads back, sorted by dataset ID independent of the save order."""
    repository = MongoCoverageRepository(db)
    coverages = [
        StoredCoverage(dataset_id=dataset_id, collection=dataset_id.replace("/", "_"), document_count=0, bucket="PT1H", updated_at=START)
        for dataset_id in ("synthetic/b", "synthetic/a")
    ]
    for coverage in coverages:
        repository.save(coverage)

    assert repository.list_all() == coverages[::-1]
