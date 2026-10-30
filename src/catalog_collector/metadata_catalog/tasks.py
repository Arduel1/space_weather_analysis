from datetime import timedelta

import requests
from pydantic_settings import BaseSettings, SettingsConfigDict
from pymongo.database import Database

from catalog_collector.cdaweb.datasets import CDAS_DATASETS_URL, cdaweb_dataset_ids_by_cdas_id, master_file_url
from catalog_collector.hapi_mirror.servers import MIRROR_BASE_URL
from catalog_collector.metadata_catalog.cdas_metadata import CdasMetadataRefresher
from catalog_collector.metadata_catalog.cdaweb_masters import CdawebMasterRefresher
from catalog_collector.metadata_catalog.hapi_refresh import MIRROR_INFOS_PATH, MetadataRefresher
from catalog_collector.metadata_catalog.spase_records import SpaseRecordRefresher
from catalog_collector.scheduling.models import SINGLE_REQUEST_TARGET, MetadataTask, PlannedRequest
from catalog_collector.scheduling.worker import TaskKind
from catalog_collector.spase.records import spase_record_url
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository
from space_weather_analysis.util.error_handling import DataRetrievalError

HAPI_METADATA_KIND = "hapi_mirror_metadata"
CDAS_METADATA_KIND = "cdas_metadata"
CDAWEB_MASTER_KIND = "cdaweb_master"
SPASE_RECORD_KIND = "spase_record"


class MetadataCatalogTaskSettings(BaseSettings):
    """Intervals of the metadata catalog task kinds, read from environment variables `METADATA_WORKER_{field name}` in upper case.

    Attributes:
        hapi_mirror_interval_hours: The time between two reads of the HAPI mirror's full file.
        cdas_interval_hours: The time between two reads of the CDAS dataset list.
        cdaweb_master_interval_hours: The time between two reads of the master file of one CDAWeb dataset.
        spase_interval_hours: The time between two reads of the SPASE record of one CDAWeb dataset.
    """

    model_config = SettingsConfigDict(env_prefix="METADATA_WORKER_", frozen=True)

    hapi_mirror_interval_hours: float = 24.0
    cdas_interval_hours: float = 24.0
    cdaweb_master_interval_hours: float = 168.0
    spase_interval_hours: float = 168.0


def hapi_metadata_kind(db: Database, settings: MetadataCatalogTaskSettings, mirror_base_url: str = MIRROR_BASE_URL) -> TaskKind:
    """Builds the task kind that refreshes the HAPI metadata from the full file of the HAPI metadata mirror.

    Args:
        db: The MongoDB database instance.
        settings: The intervals of the metadata catalog task kinds.
        mirror_base_url: The base URL of the mirror files.

    Returns:
        The kind with a single task reading the mirror's full file.
    """
    catalog_repository = MongoCatalogRepository(db)
    metadata_repository = MongoMetadataRepository(db)
    base_url = mirror_base_url.rstrip("/")

    def refresh_metadata(_task: MetadataTask) -> None:
        """Refreshes the HAPI metadata from the mirror's full file."""
        if MetadataRefresher(catalog_repository, metadata_repository, base_url).refresh() is None:
            raise DataRetrievalError("HAPI mirror file could not be read")

    return TaskKind(
        HAPI_METADATA_KIND,
        timedelta(hours=settings.hapi_mirror_interval_hours),
        lambda: [PlannedRequest(SINGLE_REQUEST_TARGET, f"{base_url}/{MIRROR_INFOS_PATH}")],
        refresh_metadata,
    )


def cdas_metadata_kind(db: Database, settings: MetadataCatalogTaskSettings, url: str = CDAS_DATASETS_URL) -> TaskKind:
    """Builds the task kind that refreshes the CDAS fields of the CDAWeb dataset records from the CDAS dataset list.

    Args:
        db: The MongoDB database instance.
        settings: The intervals of the metadata catalog task kinds.
        url: The location of the CDAS dataset list.

    Returns:
        The kind with a single task reading the dataset list.
    """
    catalog_repository = MongoCatalogRepository(db)
    metadata_repository = MongoMetadataRepository(db)
    session = requests.Session()

    def refresh_metadata(_task: MetadataTask) -> None:
        """Refreshes the CDAS entries and their overlays; a failed request raises DataRetrievalError."""
        CdasMetadataRefresher(catalog_repository, metadata_repository, session, url).refresh()

    return TaskKind(
        CDAS_METADATA_KIND,
        timedelta(hours=settings.cdas_interval_hours),
        lambda: [PlannedRequest(SINGLE_REQUEST_TARGET, url)],
        refresh_metadata,
    )


def cdaweb_master_kind(db: Database, settings: MetadataCatalogTaskSettings) -> TaskKind:
    """Builds the task kind that refreshes the master file of every CDAWeb dataset in the catalog, one task per dataset.

    Args:
        db: The MongoDB database instance.
        settings: The intervals of the metadata catalog task kinds.

    Returns:
        The kind with one task per CDAS dataset ID whose stored CDAS entry links a master file.
    """
    catalog_repository = MongoCatalogRepository(db)
    metadata_repository = MongoMetadataRepository(db)
    refresher = CdawebMasterRefresher(catalog_repository, metadata_repository, requests.Session())

    def refresh_master(task: MetadataTask) -> None:
        """Refreshes the master file of the task's dataset; a failed request raises DataRetrievalError."""
        refresher.refresh(task.target, task.url)

    return TaskKind(
        CDAWEB_MASTER_KIND,
        timedelta(hours=settings.cdaweb_master_interval_hours),
        lambda: plan_cdaweb_masters(catalog_repository, metadata_repository),
        refresh_master,
    )


def plan_cdaweb_masters(catalog_repository: CatalogRepository, metadata_repository: MetadataRepository) -> list[PlannedRequest]:
    """Lists one master file request per CDAWeb dataset of the catalog, located through the stored CDAS entry.

    Args:
        catalog_repository: The master catalog naming the CDAWeb datasets.
        metadata_repository: The stored CDAS entries, which link the master files.

    Returns:
        One request per CDAS dataset ID; datasets without stored CDAS entry or master file link are left out until the CDAS refresh finds one.
    """
    cdas_ids = sorted(cdaweb_dataset_ids_by_cdas_id(catalog_repository))
    raw_cdas_entries = metadata_repository.get_raw_many(cdas_ids, "cdas")
    planned_requests: list[PlannedRequest] = []
    for cdas_id in cdas_ids:
        raw_cdas_entry = raw_cdas_entries.get(cdas_id)
        url = master_file_url(raw_cdas_entry.payload) if raw_cdas_entry is not None else None
        if url:
            planned_requests.append(PlannedRequest(cdas_id, url))
    return planned_requests


def spase_record_kind(db: Database, settings: MetadataCatalogTaskSettings) -> TaskKind:
    """Builds the task kind that refreshes the SPASE record of every CDAWeb dataset in the catalog, one task per dataset.

    Args:
        db: The MongoDB database instance.
        settings: The intervals of the metadata catalog task kinds.

    Returns:
        The kind with one task per CDAS dataset ID whose stored CDAS entry names a SPASE resource ID.
    """
    catalog_repository = MongoCatalogRepository(db)
    metadata_repository = MongoMetadataRepository(db)
    refresher = SpaseRecordRefresher(catalog_repository, metadata_repository, requests.Session())

    def refresh_record(task: MetadataTask) -> None:
        """Refreshes the SPASE record of the task's dataset; a failed request raises DataRetrievalError."""
        refresher.refresh(task.target, task.url)

    return TaskKind(
        SPASE_RECORD_KIND,
        timedelta(hours=settings.spase_interval_hours),
        lambda: plan_spase_records(catalog_repository, metadata_repository),
        refresh_record,
    )


def plan_spase_records(catalog_repository: CatalogRepository, metadata_repository: MetadataRepository) -> list[PlannedRequest]:
    """Lists one SPASE record request per CDAWeb dataset of the catalog, located through the SPASE resource ID of the stored CDAS entry.

    Args:
        catalog_repository: The master catalog naming the CDAWeb datasets.
        metadata_repository: The stored CDAS entries, which name the SPASE resource IDs.

    Returns:
        One request per CDAS dataset ID; datasets without stored CDAS entry or resolvable SPASE resource ID are left out.
    """
    cdas_ids = sorted(cdaweb_dataset_ids_by_cdas_id(catalog_repository))
    raw_cdas_entries = metadata_repository.get_raw_many(cdas_ids, "cdas")
    planned_requests: list[PlannedRequest] = []
    for cdas_id in cdas_ids:
        raw_cdas_entry = raw_cdas_entries.get(cdas_id)
        url = spase_record_url(raw_cdas_entry.payload.get("SpaseResourceId")) if raw_cdas_entry is not None else None
        if url:
            planned_requests.append(PlannedRequest(cdas_id, url))
    return planned_requests
