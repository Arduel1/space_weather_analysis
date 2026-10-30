from datetime import timedelta

import requests
from pymongo.database import Database

from catalog_collector.cdas.datasets import CDAS_DATASETS_URL
from catalog_collector.data_catalog.cdas_availability import CdasAvailabilityRefresher
from catalog_collector.data_catalog.hapi_availability import MIRROR_DATASET_TABLE_PATH, AvailabilityRefresher
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


def cdas_availability_kind(db: Database, settings: WorkerSettings, url: str = CDAS_DATASETS_URL) -> TaskKind:
    """Builds the task kind that refreshes the availability of the CDAWeb sources ingested through SunPy Fido from the CDAS dataset list.

    Args:
        db: The MongoDB database instance.
        settings: The pacing, which sets the interval.
        url: The location of the CDAS dataset list.

    Returns:
        The kind with a single task reading the dataset list.
    """
    catalog_repository = MongoCatalogRepository(db)
    session = requests.Session()

    def refresh_availability(task: MetadataTask) -> None:
        """Refreshes the availability of the FIDO_CDAWEB sources; a failed request raises CdasRequestError."""
        CdasAvailabilityRefresher(catalog_repository, session, url).refresh()

    return TaskKind("cdas_availability", timedelta(hours=settings.cdas_interval_hours), lambda: [PlannedRequest("all", url)], refresh_availability)
