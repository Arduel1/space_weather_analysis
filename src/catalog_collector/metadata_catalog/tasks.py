from datetime import timedelta

from pymongo.database import Database

from catalog_collector.hapi_mirror.servers import MIRROR_BASE_URL
from catalog_collector.metadata_catalog.hapi_refresh import MIRROR_INFOS_PATH, MetadataRefresher
from catalog_collector.scheduling.models import MetadataTask, PlannedRequest
from catalog_collector.scheduling.worker import TaskFailedError, TaskKind, WorkerSettings
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.repository import MongoMetadataRepository


def hapi_metadata_kind(db: Database, settings: WorkerSettings, mirror_base_url: str = MIRROR_BASE_URL) -> TaskKind:
    """Builds the task kind that refreshes the HAPI metadata from the full file of the HAPI metadata mirror.

    Args:
        db: The MongoDB database instance.
        settings: The pacing, which sets the interval.
        mirror_base_url: The base URL of the mirror files.

    Returns:
        The kind with a single task reading the mirror's full file.
    """
    catalog_repository = MongoCatalogRepository(db)
    metadata_repository = MongoMetadataRepository(db)
    base_url = mirror_base_url.rstrip("/")

    def refresh_metadata(task: MetadataTask) -> None:
        """Refreshes the HAPI metadata from the mirror's full file."""
        if MetadataRefresher(catalog_repository, metadata_repository, base_url).refresh() is None:
            raise TaskFailedError("HAPI mirror file could not be read")

    return TaskKind(
        "hapi_mirror_metadata",
        timedelta(hours=settings.hapi_mirror_interval_hours),
        lambda: [PlannedRequest("all", f"{base_url}/{MIRROR_INFOS_PATH}")],
        refresh_metadata,
    )
