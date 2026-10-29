from datetime import timedelta

from pymongo.database import Database

from catalog_collector.data_catalog.availability import MIRROR_DATASET_TABLE_PATH, AvailabilityRefresher
from catalog_collector.hapi_mirror.servers import MIRROR_BASE_URL
from catalog_collector.scheduling.models import MetadataTask, PlannedRequest
from catalog_collector.scheduling.worker import TaskFailedError, TaskKind, WorkerSettings
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.repository import MongoMetadataRepository


def hapi_availability_kind(db: Database, settings: WorkerSettings, mirror_base_url: str = MIRROR_BASE_URL) -> TaskKind:
    """Builds the task kind that refreshes the availability of the HAPI sources from the HAPI metadata mirror.

    Args:
        db: The MongoDB database instance.
        settings: The pacing, which sets the interval.
        mirror_base_url: The base URL of the mirror files.

    Returns:
        The kind with a single task reading the mirror's catalog and dataset table.
    """
    catalog_repository = MongoCatalogRepository(db)
    metadata_repository = MongoMetadataRepository(db)
    base_url = mirror_base_url.rstrip("/")

    def refresh_availability(task: MetadataTask) -> None:
        """Refreshes the availability of the HAPI sources from the mirror's catalog and dataset table."""
        if AvailabilityRefresher(catalog_repository, metadata_repository, base_url).refresh() is None:
            raise TaskFailedError("HAPI mirror catalog or dataset table could not be read")

    return TaskKind(
        "hapi_mirror_availability",
        timedelta(hours=settings.hapi_mirror_interval_hours),
        lambda: [PlannedRequest("all", f"{base_url}/{MIRROR_DATASET_TABLE_PATH}")],
        refresh_availability,
    )
