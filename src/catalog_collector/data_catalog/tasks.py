from datetime import timedelta

import requests
from pydantic_settings import BaseSettings, SettingsConfigDict
from pymongo.database import Database

from catalog_collector.cdaweb.datasets import CDAS_DATASETS_URL
from catalog_collector.data_catalog.cdas_availability import CdasAvailabilityRefresher
from catalog_collector.data_catalog.hapi_availability import MIRROR_DATASET_TABLE_PATH, AvailabilityRefresher
from catalog_collector.hapi_mirror.servers import MIRROR_BASE_URL
from catalog_collector.scheduling.models import SINGLE_REQUEST_TARGET, MetadataTask, PlannedRequest
from catalog_collector.scheduling.worker import TaskKind
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.util.error_handling import DataRetrievalError

HAPI_AVAILABILITY_KIND = "hapi_mirror_availability"
CDAS_AVAILABILITY_KIND = "cdas_availability"


class DataCatalogTaskSettings(BaseSettings):
    """Intervals of the data catalog task kinds, read from environment variables `METADATA_WORKER_{field name}` in upper case.

    Attributes:
        hapi_mirror_interval_hours: The time between two reads of the HAPI mirror catalog and dataset table.
        cdas_interval_hours: The time between two reads of the CDAS dataset list.
    """

    model_config = SettingsConfigDict(env_prefix="METADATA_WORKER_", frozen=True)

    hapi_mirror_interval_hours: float = 24.0
    cdas_interval_hours: float = 24.0


def hapi_availability_kind(db: Database, settings: DataCatalogTaskSettings, mirror_base_url: str = MIRROR_BASE_URL) -> TaskKind:
    """Builds the task kind that refreshes the availability of the HAPI sources from the HAPI metadata mirror.

    Args:
        db: The MongoDB database instance.
        settings: The intervals of the data catalog task kinds.
        mirror_base_url: The base URL of the mirror files.

    Returns:
        The kind with a single task reading the mirror's catalog and dataset table.
    """
    catalog_repository = MongoCatalogRepository(db)
    metadata_repository = MongoMetadataRepository(db)
    base_url = mirror_base_url.rstrip("/")

    def refresh_availability(_task: MetadataTask) -> None:
        """Refreshes the availability of the HAPI sources from the mirror's catalog and dataset table."""
        if AvailabilityRefresher(catalog_repository, metadata_repository, base_url).refresh() is None:
            raise DataRetrievalError("HAPI mirror catalog or dataset table could not be read")

    return TaskKind(
        HAPI_AVAILABILITY_KIND,
        timedelta(hours=settings.hapi_mirror_interval_hours),
        lambda: [PlannedRequest(SINGLE_REQUEST_TARGET, f"{base_url}/{MIRROR_DATASET_TABLE_PATH}")],
        refresh_availability,
    )


def cdas_availability_kind(db: Database, settings: DataCatalogTaskSettings, url: str = CDAS_DATASETS_URL) -> TaskKind:
    """Builds the task kind that refreshes the availability of the CDAWeb sources ingested through SunPy Fido from the CDAS dataset list.

    Args:
        db: The MongoDB database instance.
        settings: The intervals of the data catalog task kinds.
        url: The location of the CDAS dataset list.

    Returns:
        The kind with a single task reading the dataset list.
    """
    catalog_repository = MongoCatalogRepository(db)
    session = requests.Session()

    def refresh_availability(_task: MetadataTask) -> None:
        """Refreshes the availability of the FIDO_CDAWEB sources; a failed request raises DataRetrievalError."""
        CdasAvailabilityRefresher(catalog_repository, session, url).refresh()

    return TaskKind(
        CDAS_AVAILABILITY_KIND,
        timedelta(hours=settings.cdas_interval_hours),
        lambda: [PlannedRequest(SINGLE_REQUEST_TARGET, url)],
        refresh_availability,
    )
