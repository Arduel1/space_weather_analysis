import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import batched

from catalog_collector.cdaweb.datasets import CDAS_DATASETS_URL, CdasDataset, cdaweb_dataset_ids_by_cdas_id
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.metadata.models import MetadataSourceKind, RawMetadataRecord
from space_weather_analysis.metadata.overlays.layering import resave_cdaweb_records
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
        self,
        catalog_repository: CatalogRepository,
        metadata_repository: MetadataRepository,
        url: str = CDAS_DATASETS_URL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initializes the refresher.

        Args:
            catalog_repository: The master catalog naming the CDAWeb datasets.
            metadata_repository: The raw metadata and records to refresh.
            url: The location of the CDAS dataset list, recorded with the stored entries.
            clock: Returns the current time in UTC.
        """
        self.catalog_repository = catalog_repository
        self.metadata_repository = metadata_repository
        self.url = url
        self.clock = clock

    def refresh(self, cdas_datasets: dict[str, CdasDataset]) -> CdasMetadataSummary:
        """Stores every new or changed CDAS entry of a catalog dataset and saves its records again with the overlay.

        Args:
            cdas_datasets: The CDAS entries keyed by dataset ID.

        Returns:
            The summary.
        """
        datasets = cdaweb_dataset_ids_by_cdas_id(self.catalog_repository)
        retrieved_at = self.clock()
        summary = CdasMetadataSummary()

        listed_cdas_ids = [cdas_id for cdas_id in sorted(datasets) if cdas_id in cdas_datasets]
        summary.missing = len(datasets) - len(listed_cdas_ids)
        for batch in batched(listed_cdas_ids, BATCH_SIZE):
            self._refresh_batch(batch, cdas_datasets, datasets, retrieved_at, summary)

        logger.info(
            "CDAS metadata refresh: %d entries saved, %d unchanged, %d not listed, %d records overlaid",
            summary.saved,
            summary.unchanged,
            summary.missing,
            summary.records_overlaid,
        )
        return summary

    def _refresh_batch(
        self,
        batch: Sequence[str],
        cdas_datasets: dict[str, CdasDataset],
        datasets: dict[str, list[str]],
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
        stored_raw_records = self.metadata_repository.get_raw_many(batch, MetadataSourceKind.CDAS)
        new_raw_records: list[RawMetadataRecord] = []
        changed_dataset_ids: list[str] = []
        for cdas_id in batch:
            payload = sanitize_for_bson(cdas_datasets[cdas_id].entry)
            stored_raw_record = stored_raw_records.get(cdas_id)
            if stored_raw_record is not None and stored_raw_record.payload == payload:
                summary.unchanged += 1
                continue
            new_raw_records.append(
                RawMetadataRecord(dataset_id=cdas_id, kind=MetadataSourceKind.CDAS, payload=payload, url=self.url, retrieved_at=retrieved_at)
            )
            changed_dataset_ids.extend(datasets[cdas_id])

        # The raw entries come first, as the overlay of the saved records reads them
        self.metadata_repository.save_raw_many(new_raw_records)
        summary.saved += len(new_raw_records)
        summary.records_overlaid += resave_cdaweb_records(self.metadata_repository, changed_dataset_ids)
