import logging
from collections.abc import Callable
from datetime import UTC, datetime

import requests

from catalog_collector.cdaweb.datasets import cdaweb_dataset_ids_by_cdas_id
from catalog_collector.spase.records import fetch_spase_record
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.metadata.overlays import resave_cdaweb_records
from space_weather_analysis.metadata.repository import MetadataRepository, sanitize_for_bson

logger = logging.getLogger(__name__)


class SpaseRecordRefresher:
    """Keeps the SPASE record of one CDAWeb dataset current and lays it over the records of the dataset.

    The SPASE record is stored as raw metadata of kind "spase" under the CDAS dataset ID, which every
    HAPI split `{dataset_id}@{index}` of the dataset shares. Stored metadata is never deleted.
    """

    def __init__(
        self,
        catalog_repository: CatalogRepository,
        metadata_repository: MetadataRepository,
        session: requests.Session,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initializes the refresher.

        Args:
            catalog_repository: The master catalog naming the CDAWeb datasets.
            metadata_repository: The raw metadata and records to refresh.
            session: The HTTP session for the requests.
            clock: Returns the current time in UTC.
        """
        self.catalog_repository = catalog_repository
        self.metadata_repository = metadata_repository
        self.session = session
        self.clock = clock

    def refresh(self, cdas_id: str, url: str) -> bool:
        """Stores the SPASE record of one dataset if it is new or changed and saves the records of the dataset again.

        Args:
            cdas_id: The CDAS dataset ID.
            url: The location of the SPASE record in JSON form.

        Returns:
            True if the stored SPASE record changed; False if it is unchanged or no record exists at the location.

        Raises:
            DataRetrievalError, DataProcessingError: If the record cannot be read; the stored metadata is then left unchanged.
        """
        record = fetch_spase_record(self.session, url)
        if record is None:
            logger.info("No SPASE record of %s at %s.", cdas_id, url)
            return False
        payload = sanitize_for_bson(record)
        stored = self.metadata_repository.get_raw(cdas_id, "spase")
        if stored is not None and stored.payload == payload:
            return False

        # The raw record comes first, as the overlay of the saved records reads it
        self.metadata_repository.save_raw(cdas_id, "spase", payload, url, self.clock())
        dataset_ids = cdaweb_dataset_ids_by_cdas_id(self.catalog_repository).get(cdas_id, [])
        overlaid = resave_cdaweb_records(self.metadata_repository, dataset_ids)
        logger.info("SPASE record of %s saved; %d records overlaid.", cdas_id, overlaid)
        return True
