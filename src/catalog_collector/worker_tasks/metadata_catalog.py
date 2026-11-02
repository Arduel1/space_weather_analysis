from collections.abc import Callable
from datetime import timedelta
from typing import Any

import requests
from pymongo.database import Database

from catalog_collector.cdaweb.datasets import cdaweb_dataset_ids_by_cdas_id, master_file_url
from catalog_collector.hapi_mirror.files import MIRROR_BASE_URL, MIRROR_INFOS_PATH, mirror_file_url
from catalog_collector.hapi_mirror.servers import first_hapi_servers
from catalog_collector.metadata_catalog.cdaweb_masters import CdawebMasterRefresher
from catalog_collector.metadata_catalog.hapi_coverage import sync_hapi_coverage
from catalog_collector.metadata_catalog.hapi_metadata import HapiMirrorMetadataRefresher
from catalog_collector.metadata_catalog.spase_records import SpaseRecordRefresher
from catalog_collector.scheduling.models import SINGLE_REQUEST_TARGET, MetadataTask, PlannedRequest, TaskKind
from catalog_collector.spase.records import spase_record_url
from catalog_collector.worker_tasks.settings import TaskIntervalSettings
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.metadata.models import MetadataSourceKind
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository
from space_weather_analysis.util.error_handling import DataRetrievalError

HAPI_METADATA_KIND = "hapi_mirror_metadata"
CDAWEB_MASTER_KIND = "cdaweb_master"
SPASE_RECORD_KIND = "spase_record"


class CdawebDatasetPlan:
    """Plans one request per CDAWeb dataset from the stored CDAS entries and remembers which catalog datasets each serves.

    The catalog datasets of each CDAS dataset ID are read once per plan, so a run of a single task needs no catalog scan.

    Attributes:
        dataset_ids_by_cdas_id: The catalog dataset IDs of each CDAS dataset ID, as of the last plan.
    """

    def __init__(
        self,
        catalog_repository: CatalogRepository,
        metadata_repository: MetadataRepository,
        url_of_entry: Callable[[dict[str, Any]], str | None],
    ) -> None:
        """Initializes the plan.

        Args:
            catalog_repository: The master catalog naming the CDAWeb datasets.
            metadata_repository: The stored CDAS entries, which locate the requested files.
            url_of_entry: Reads the location of the requested file from a CDAS entry; None if the entry has none.
        """
        self.catalog_repository = catalog_repository
        self.metadata_repository = metadata_repository
        self.url_of_entry = url_of_entry
        self.dataset_ids_by_cdas_id: dict[str, list[str]] = {}

    def plan(self) -> list[PlannedRequest]:
        """Lists one request per CDAS dataset ID whose stored CDAS entry locates a file.

        Returns:
            The requests in CDAS dataset ID order; datasets without stored CDAS entry or location are left out until the CDAS refresh finds one.
        """
        self.dataset_ids_by_cdas_id = cdaweb_dataset_ids_by_cdas_id(self.catalog_repository)
        cdas_ids = sorted(self.dataset_ids_by_cdas_id)
        raw_cdas_entries = self.metadata_repository.get_raw_many(cdas_ids, MetadataSourceKind.CDAS)
        planned_requests: list[PlannedRequest] = []
        for cdas_id in cdas_ids:
            raw_cdas_entry = raw_cdas_entries.get(cdas_id)
            url = self.url_of_entry(raw_cdas_entry.payload) if raw_cdas_entry is not None else None
            if url:
                planned_requests.append(PlannedRequest(cdas_id, url))
        return planned_requests

    def dataset_ids(self, cdas_id: str) -> list[str]:
        """Returns the catalog datasets of one CDAS dataset ID as of the last plan.

        Args:
            cdas_id: The CDAS dataset ID.

        Returns:
            The catalog dataset IDs.
        """
        return self.dataset_ids_by_cdas_id.get(cdas_id, [])


def hapi_metadata_kind(db: Database, settings: TaskIntervalSettings, mirror_base_url: str = MIRROR_BASE_URL) -> TaskKind:
    """Builds the task kind that refreshes the HAPI metadata from the full file of the HAPI metadata mirror.

    After each refresh the coverage of the HAPI records follows the time ranges of the availability refresh.

    Args:
        db: The MongoDB database instance.
        settings: The intervals of the task kinds.
        mirror_base_url: The base URL of the mirror files.

    Returns:
        The kind with a single task reading the mirror's full file.
    """
    catalog_repository = MongoCatalogRepository(db)
    metadata_repository = MongoMetadataRepository(db)
    refresher = HapiMirrorMetadataRefresher(catalog_repository, metadata_repository, requests.Session(), mirror_base_url)

    def refresh_metadata(_task: MetadataTask) -> None:
        """Refreshes the HAPI metadata and the coverage; a failed request raises DataRetrievalError or DataProcessingError."""
        # On a fresh database the availability task builds the HAPI sources; failing until then retries after the
        # failure backoff instead of a full interval, and skips the download of the large mirror file
        if not first_hapi_servers(catalog_repository):
            raise DataRetrievalError("The master catalog holds no HAPI source yet; the HAPI availability refresh adds them")
        refresher.refresh()
        sync_hapi_coverage(catalog_repository, metadata_repository)

    return TaskKind(
        HAPI_METADATA_KIND,
        timedelta(hours=settings.hapi_mirror_interval_hours),
        lambda: [PlannedRequest(SINGLE_REQUEST_TARGET, mirror_file_url(mirror_base_url, MIRROR_INFOS_PATH))],
        refresh_metadata,
    )


def cdaweb_master_kind(db: Database, settings: TaskIntervalSettings) -> TaskKind:
    """Builds the task kind that refreshes the master file of every CDAWeb dataset in the catalog, one task per dataset.

    Args:
        db: The MongoDB database instance.
        settings: The intervals of the task kinds.

    Returns:
        The kind with one task per CDAS dataset ID whose stored CDAS entry links a master file.
    """
    metadata_repository = MongoMetadataRepository(db)
    plan = CdawebDatasetPlan(MongoCatalogRepository(db), metadata_repository, master_file_url)
    refresher = CdawebMasterRefresher(metadata_repository, requests.Session())

    def refresh_master(task: MetadataTask) -> None:
        """Refreshes the master file of the task's dataset; a failed request raises DataRetrievalError or DataProcessingError."""
        refresher.refresh(task.target, task.url, plan.dataset_ids(task.target))

    return TaskKind(CDAWEB_MASTER_KIND, timedelta(hours=settings.cdaweb_master_interval_hours), plan.plan, refresh_master)


def spase_record_kind(db: Database, settings: TaskIntervalSettings) -> TaskKind:
    """Builds the task kind that refreshes the SPASE record of every CDAWeb dataset in the catalog, one task per dataset.

    Args:
        db: The MongoDB database instance.
        settings: The intervals of the task kinds.

    Returns:
        The kind with one task per CDAS dataset ID whose stored CDAS entry names a SPASE resource ID.
    """
    metadata_repository = MongoMetadataRepository(db)
    plan = CdawebDatasetPlan(MongoCatalogRepository(db), metadata_repository, spase_record_url_of_entry)
    refresher = SpaseRecordRefresher(metadata_repository, requests.Session())

    def refresh_record(task: MetadataTask) -> None:
        """Refreshes the SPASE record of the task's dataset; a failed request raises DataRetrievalError or DataProcessingError."""
        refresher.refresh(task.target, task.url, plan.dataset_ids(task.target))

    return TaskKind(SPASE_RECORD_KIND, timedelta(hours=settings.spase_interval_hours), plan.plan, refresh_record)


def spase_record_url_of_entry(entry: dict[str, Any]) -> str | None:
    """Reads the location of the SPASE record of a dataset from its CDAS entry.

    Args:
        entry: The dataset entry as listed by CDAS.

    Returns:
        The URL of the SPASE record in JSON form, or None if the entry names no resolvable SPASE resource ID.
    """
    return spase_record_url(entry.get("SpaseResourceId"))
