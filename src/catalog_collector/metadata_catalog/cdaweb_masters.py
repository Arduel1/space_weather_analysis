from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import requests

from catalog_collector.cdaweb.masters import fetch_master
from catalog_collector.metadata_catalog.raw_updates import store_changed_raw_and_overlay
from space_weather_analysis.metadata.repository import MetadataRepository


class CdawebMasterRefresher:
    """Keeps the master file of one CDAWeb dataset current and lays it over the records of the dataset.

    The master file is stored as raw metadata of kind "istp" under the CDAS dataset ID, which every
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
        """Stores the master file of one dataset if it is new or changed and saves the records of the dataset again.

        Args:
            cdas_id: The CDAS dataset ID.
            url: The location of the master file in JSON format.
            dataset_ids: The catalog datasets that share the CDAS dataset ID.

        Returns:
            True if the stored master file changed.

        Raises:
            DataRetrievalError, DataProcessingError: If the master file cannot be read; the stored metadata is then left unchanged.
        """
        master = fetch_master(self.session, url)
        return store_changed_raw_and_overlay(self.metadata_repository, "istp", cdas_id, master, url, self.clock(), dataset_ids)
