from datetime import timedelta

import requests
from pymongo.database import Database

from catalog_collector.data_catalog.hapi_availability import HapiMirrorAvailabilityRefresher
from catalog_collector.hapi_mirror.files import MIRROR_BASE_URL, MIRROR_DATASET_TABLE_PATH, mirror_file_url
from catalog_collector.scheduling.models import SINGLE_REQUEST_TARGET, MetadataTask, PlannedRequest, TaskKind
from catalog_collector.worker_tasks.settings import TaskIntervalSettings
from space_weather_analysis.catalog.repository import MongoCatalogRepository

HAPI_AVAILABILITY_KIND = "hapi_mirror_availability"


def hapi_availability_kind(db: Database, settings: TaskIntervalSettings, mirror_base_url: str = MIRROR_BASE_URL) -> TaskKind:
    """Builds the task kind that refreshes the availability of the HAPI sources from the HAPI metadata mirror.

    Args:
        db: The MongoDB database instance.
        settings: The intervals of the task kinds.
        mirror_base_url: The base URL of the mirror files.

    Returns:
        The kind with a single task reading the mirror's catalog and dataset table.
    """
    refresher = HapiMirrorAvailabilityRefresher(MongoCatalogRepository(db), requests.Session(), mirror_base_url)

    def refresh_availability(_task: MetadataTask) -> None:
        """Refreshes the availability of the HAPI sources; a failed request raises DataRetrievalError or DataProcessingError."""
        refresher.refresh()

    return TaskKind(
        HAPI_AVAILABILITY_KIND,
        timedelta(hours=settings.hapi_mirror_interval_hours),
        lambda: [PlannedRequest(SINGLE_REQUEST_TARGET, mirror_file_url(mirror_base_url, MIRROR_DATASET_TABLE_PATH))],
        refresh_availability,
    )
