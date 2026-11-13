import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from pymongo.database import Database

from pipeline_service.feeds.models import Feed
from pipeline_service.feeds.scheduler import queue_feed_jobs
from pipeline_service.main import run_once, save_static_catalog
from space_weather_analysis.catalog.models import CatalogEntryDict, SourceType, TimeRangeDict
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.catalog.static_entries import load_static_catalog
from space_weather_analysis.jobs.models import JobStatus
from space_weather_analysis.jobs.repository import MongoJobRepository

NOW = datetime(2024, 3, 1, tzinfo=UTC)
CATALOG_RANGE: TimeRangeDict = {"start_time": "1818-01-01T00:00:00Z", "end_time": "2024-03-01T00:00:00Z"}
FEEDS = [
    Feed(name="kp", datasets=["Kp"], lookback=timedelta(days=2)),
    Feed(name="local", datasets=["silso_sunspot_daily"], source_type=SourceType.LOCAL),
]


@pytest.mark.integration
def test_feed_jobs_are_queued_with_their_range_and_once_while_open(db: Database) -> None:
    """Each feed gets a pending job with its resolved range; a second start queues nothing for feeds whose job is still open."""
    repository = MongoJobRepository(db)

    queue_feed_jobs(repository, FEEDS, NOW)
    queue_feed_jobs(repository, FEEDS, NOW + timedelta(hours=1))

    jobs = [repository.claim_next_pending(), repository.claim_next_pending(), repository.claim_next_pending()]
    assert jobs[2] is None
    assert [(job.feed, job.source_type, job.parameters.start_time, job.parameters.end_time) for job in jobs[:2] if job] == [
        ("kp", None, NOW - timedelta(days=2), NOW),
        ("local", SourceType.LOCAL, None, None),
    ]


@pytest.mark.integration
def test_feed_whose_job_ended_gets_a_new_job(db: Database) -> None:
    """Once the job of a feed has ended, the next start queues a new one."""
    repository = MongoJobRepository(db)
    queue_feed_jobs(repository, FEEDS[:1], NOW)
    first_job = repository.claim_next_pending()
    assert first_job is not None
    repository.finish(first_job.id, JobStatus.COMPLETED)

    queue_feed_jobs(repository, FEEDS[:1], NOW + timedelta(days=1))

    second_job = repository.claim_next_pending()
    assert second_job is not None and second_job.parameters.end_time == NOW + timedelta(days=1)


@pytest.mark.integration
def test_static_catalog_items_are_saved_only_for_datasets_the_catalog_lacks(db: Database, tmp_path: pathlib.Path) -> None:
    """A configured item is inserted once; a stored item keeps its sources and only gains the cadence of a matching configured source."""
    catalog_file = tmp_path / "catalog.toml"
    catalog_file.write_text(
        "[[dataset]]\n"
        'dataset_id = "hour_dst"\n'
        'sources = [{ type = "HAPI", source_id = "https://kyoto.example/hapi", time_range = { start_time = "1957-01-01T00:00:00Z",'
        ' end_time = "2024-01-01T00:00:00Z" }, ongoing = true, cadence = "PT1H" }]\n'
        "[[dataset]]\n"
        'dataset_id = "OMNI2_H0_MRG1HR"\n'
        'sources = [{ type = "HAPI", source_id = "https://configured.example/hapi", time_range = { start_time = "1963-01-01T00:00:00Z",'
        ' end_time = "2024-01-01T00:00:00Z" } }]\n'
        "[[dataset]]\n"
        'dataset_id = "min_asysym"\n'
        'sources = [{ type = "HAPI", source_id = "https://worker.example/hapi", time_range = { start_time = "1981-01-01T00:00:00Z",'
        ' end_time = "2024-01-01T00:00:00Z" }, cadence = "PT1M" }]\n',
        encoding="utf-8",
    )
    repository = MongoCatalogRepository(db)
    worker_sources: list[CatalogEntryDict] = [{"type": "HAPI", "source_id": "https://worker.example/hapi", "time_range": CATALOG_RANGE}]
    repository.save({"dataset_id": "OMNI2_H0_MRG1HR", "sources": worker_sources})
    repository.save({"dataset_id": "min_asysym", "sources": worker_sources})

    save_static_catalog(repository, load_static_catalog(catalog_file))
    save_static_catalog(repository, load_static_catalog(catalog_file))

    assert repository.get_sources("OMNI2_H0_MRG1HR") == worker_sources
    assert repository.get_sources("min_asysym") == [{**worker_sources[0], "cadence": "PT1M"}]
    assert repository.get_sources("hour_dst") == [
        {
            "type": "HAPI",
            "source_id": "https://kyoto.example/hapi",
            "time_range": {"start_time": "1957-01-01T00:00:00Z", "end_time": "2024-01-01T00:00:00Z"},
            "ongoing": True,
            "cadence": "PT1H",
        }
    ]


@pytest.mark.integration
def test_run_once_leaves_out_a_feed_whose_datasets_the_catalog_lacks(db: Database, sunspot_dir: pathlib.Path) -> None:
    """A feed without source type runs only if the catalog holds every one of its datasets."""
    MongoCatalogRepository(db).save(
        {"dataset_id": "silso_sunspot_daily", "sources": [{"type": "LOCAL", "source_id": "local", "time_range": CATALOG_RANGE}]}
    )
    feeds = [
        Feed(name="sunspots", datasets=["silso_sunspot_daily"]),
        Feed(name="partly_catalogued", datasets=["silso_sunspot_daily", "hour_dst"]),
    ]

    run_once(db, MongoJobRepository(db), feeds, sunspot_dir, NOW)

    assert [(job["feed"], job["status"]) for job in db[MongoJobRepository.COLLECTION].find()] == [("sunspots", JobStatus.COMPLETED)]
