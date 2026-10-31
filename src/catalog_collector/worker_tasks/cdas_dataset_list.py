from datetime import timedelta

import requests
from pymongo.database import Database

from catalog_collector.cdaweb.datasets import CDAS_DATASETS_URL, fetch_cdas_datasets
from catalog_collector.data_catalog.cdas_availability import CdasAvailabilityRefresher
from catalog_collector.metadata_catalog.cdas_metadata import CdasMetadataRefresher
from catalog_collector.scheduling.models import SINGLE_REQUEST_TARGET, MetadataTask, PlannedRequest, TaskKind
from catalog_collector.worker_tasks.settings import TaskIntervalSettings
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.repository import MongoMetadataRepository

CDAS_DATASET_LIST_KIND = "cdas_dataset_list"


def cdas_dataset_list_kind(db: Database, settings: TaskIntervalSettings, url: str = CDAS_DATASETS_URL) -> TaskKind:
    """Builds the task kind that reads the CDAS dataset list once and feeds both the data catalog and the metadata catalog.

    Args:
        db: The MongoDB database instance.
        settings: The intervals of the task kinds.
        url: The location of the CDAS dataset list.

    Returns:
        The kind with a single task reading the dataset list.
    """
    catalog_repository = MongoCatalogRepository(db)
    session = requests.Session()
    availability_refresher = CdasAvailabilityRefresher(catalog_repository)
    metadata_refresher = CdasMetadataRefresher(catalog_repository, MongoMetadataRepository(db), url)

    def refresh_from_dataset_list(_task: MetadataTask) -> None:
        """Refreshes the CDAWeb availability and CDAS metadata; a failed request raises DataRetrievalError or DataProcessingError."""
        cdas_datasets = fetch_cdas_datasets(session, url)
        availability_refresher.refresh(cdas_datasets)
        metadata_refresher.refresh(cdas_datasets)

    return TaskKind(
        CDAS_DATASET_LIST_KIND,
        timedelta(hours=settings.cdas_interval_hours),
        lambda: [PlannedRequest(SINGLE_REQUEST_TARGET, url)],
        refresh_from_dataset_list,
    )
