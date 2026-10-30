import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Sequence
from urllib.parse import urlsplit

import requests

from catalog_collector.cdas.datasets import CDAS_DATASETS_URL, CdasDataset, fetch_cdas_datasets
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.metadata.models import RawMetadataRecord
from space_weather_analysis.metadata.overlays import CDAWEB_HOST, cdas_dataset_id, describes_cdaweb_dataset, save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository, sanitize_for_bson

logger = logging.getLogger(__name__)

# CDAS datasets compared and written per round trip to the database.
BATCH_SIZE = 1000


@dataclass
class CdasMetadataSummary:
    """The outcome of one metadata refresh from CDAS.

    Attributes:
        saved: CDAS entries stored because they are new or changed.
        unchanged: CDAS entries equal to the stored raw metadata.
        missing: CDAS dataset IDs of the catalog that CDAS does not list; their stored metadata is kept.
        records_overlaid: Stored records saved again with the changed CDAS fields.
    """

    saved: int = 0
    unchanged: int = 0
    missing: int = 0
    records_overlaid: int = 0


class CdasMetadataRefresher:
    """Keeps the CDAS dataset entries of the CDAWeb datasets in the catalog current and lays them over their records.

    Stored metadata is never deleted; a dataset CDAS no longer lists keeps its last entry.
    """

    def __init__(
        self, catalog_repository: CatalogRepository, metadata_repository: MetadataRepository, session: requests.Session, url: str = CDAS_DATASETS_URL
    ) -> None:
        """Initializes the refresher.

        Args:
            catalog_repository: The master catalog naming the CDAWeb datasets.
            metadata_repository: The raw metadata and records to refresh.
            session: The HTTP session for the request.
            url: The location of the CDAS dataset list.
        """
        self.catalog_repository = catalog_repository
        self.metadata_repository = metadata_repository
        self.session = session
        self.url = url

    def refresh(self) -> CdasMetadataSummary:
        """Stores every new or changed CDAS entry of a catalog dataset and saves its records again with the overlay.

        Returns:
            The summary.

        Raises:
            CdasRequestError: If the dataset list cannot be read; the stored metadata is then left unchanged.
        """
        datasets = self._dataset_ids_by_cdas_id()
        cdas_datasets = fetch_cdas_datasets(self.session, self.url)
        retrieved_at = datetime.now(timezone.utc)
        summary = CdasMetadataSummary()

        listed_cdas_ids = [cdas_id for cdas_id in sorted(datasets) if cdas_id in cdas_datasets]
        summary.missing = len(datasets) - len(listed_cdas_ids)
        for start in range(0, len(listed_cdas_ids), BATCH_SIZE):
            batch = listed_cdas_ids[start : start + BATCH_SIZE]
            self._refresh_batch(batch, cdas_datasets, datasets, retrieved_at, summary)

        logger.info(
            f"CDAS metadata refresh: {summary.saved} entries saved, {summary.unchanged} unchanged, "
            f"{summary.missing} not listed, {summary.records_overlaid} records overlaid"
        )
        return summary

    def _dataset_ids_by_cdas_id(self) -> Dict[str, List[str]]:
        """Lists the catalog datasets that CDAWeb serves, grouped by their CDAS dataset ID.

        Returns:
            The catalog dataset IDs keyed by CDAS dataset ID; HAPI splits `{dataset_id}@{index}` share one key.
        """
        datasets: Dict[str, List[str]] = {}
        for item in self.catalog_repository.get_all():
            for source in item["sources"]:
                source_type = source.get("type")
                is_cdaweb_hapi = source_type == SourceType.HAPI and (urlsplit(source.get("source_id", "")).hostname or "").lower() == CDAWEB_HOST
                if source_type == SourceType.FIDO_CDAWEB or is_cdaweb_hapi:
                    datasets.setdefault(cdas_dataset_id(item["dataset_id"]), []).append(item["dataset_id"])
                    break
        return datasets

    def _refresh_batch(
        self,
        batch: Sequence[str],
        cdas_datasets: Dict[str, CdasDataset],
        datasets: Dict[str, List[str]],
        retrieved_at: datetime,
        summary: CdasMetadataSummary,
    ) -> None:
        """Compares one batch of CDAS entries with the stored raw metadata and saves the changed ones.

        Args:
            batch: The CDAS dataset IDs to compare.
            cdas_datasets: The CDAS entries keyed by dataset ID.
            datasets: The catalog dataset IDs keyed by CDAS dataset ID.
            retrieved_at: When the dataset list was read.
            summary: The summary to count the outcomes in, changed in place.
        """
        stored_raw_records = self.metadata_repository.get_raw_many(batch, "cdas")
        new_raw_records: List[RawMetadataRecord] = []
        changed_dataset_ids: List[str] = []
        for cdas_id in batch:
            payload = sanitize_for_bson(cdas_datasets[cdas_id].entry)
            stored_raw_record = stored_raw_records.get(cdas_id)
            if stored_raw_record is not None and stored_raw_record.payload == payload:
                summary.unchanged += 1
                continue
            new_raw_records.append(RawMetadataRecord(dataset_id=cdas_id, kind="cdas", payload=payload, url=self.url, retrieved_at=retrieved_at))
            changed_dataset_ids.extend(datasets[cdas_id])

        # The raw entries come first, as the overlay of the saved records reads them
        self.metadata_repository.save_raw_many(new_raw_records)
        # Records of another source, such as a second HAPI server, get no overlay and stay as stored
        stored_records = self.metadata_repository.get_many(changed_dataset_ids).values()
        records = [record for record in stored_records if describes_cdaweb_dataset(record)]
        save_with_overlays(self.metadata_repository, records)
        summary.saved += len(new_raw_records)
        summary.records_overlaid += len(records)
