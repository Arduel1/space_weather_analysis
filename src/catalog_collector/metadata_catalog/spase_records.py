import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import requests

from catalog_collector.metadata_catalog.raw_updates import store_changed_raw_and_overlay
from catalog_collector.spase.records import fetch_spase_record
from space_weather_analysis.metadata.models import MetadataSourceKind
from space_weather_analysis.metadata.repository import MetadataRepository

logger = logging.getLogger(__name__)


class SpaseRecordRefresher:
    """Keeps the SPASE record of one CDAWeb dataset current and lays it over the records of the dataset.

    The SPASE record is stored as raw metadata of kind "spase" under the CDAS dataset ID, which every
    HAPI split `{dataset_id}@{index}` of the dataset shares. Stored metadata is never deleted.
    """

    def __init__(
        self,
        metadata_repository: MetadataRepository,
        session: requests.Session,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initializes the refresher.

        Args:
            metadata_repository: The raw metadata and records to refresh.
            session: The HTTP session for the requests.
            clock: Returns the current time in UTC.
        """
        self.metadata_repository = metadata_repository
        self.session = session
        self.clock = clock

    def refresh(self, cdas_id: str, url: str, dataset_ids: Sequence[str]) -> bool:
        """Stores the SPASE record of one dataset if it is new or changed and saves the records of the dataset again.

        Args:
            cdas_id: The CDAS dataset ID.
            url: The location of the SPASE record in JSON form.
            dataset_ids: The catalog datasets that share the CDAS dataset ID.

        Returns:
            True if the stored SPASE record changed; False if it is unchanged or no record exists at the location.

        Raises:
            DataRetrievalError, DataProcessingError: If the record cannot be read; the stored metadata is then left unchanged.
        """
        record = fetch_spase_record(self.session, url)
        if record is None:
            logger.info("No SPASE record of %s at %s.", cdas_id, url)
            return False
        return store_changed_raw_and_overlay(self.metadata_repository, MetadataSourceKind.SPASE, cdas_id, record, url, self.clock(), dataset_ids)
