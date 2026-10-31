import logging
from collections.abc import Callable
from datetime import UTC, datetime

from catalog_collector.cdaweb.datasets import CdasDataset
from catalog_collector.data_catalog.availability_state import (
    ONGOING_TOLERANCE,
    AvailabilitySummary,
    LookupOutcome,
    SourceLookup,
    refresh_sources,
)
from space_weather_analysis.catalog.models import CatalogEntryDict, SourceType
from space_weather_analysis.catalog.repository import CatalogRepository

logger = logging.getLogger(__name__)


class CdasAvailabilityRefresher:
    """Keeps the time range of the CDAWeb sources (CDAWEB) current from the CDAS dataset list.

    Sources whose dataset CDAS no longer lists are kept and only counted as missing.
    """

    def __init__(self, catalog_repository: CatalogRepository, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """Initializes the refresher.

        Args:
            catalog_repository: The master catalog to refresh.
            clock: Returns the current time in UTC.
        """
        self.catalog_repository = catalog_repository
        self.clock = clock

    def refresh(self, cdas_datasets: dict[str, CdasDataset]) -> AvailabilitySummary:
        """Updates the availability of every CDAWEB source of the catalog from the read dataset list.

        Args:
            cdas_datasets: The CDAS entries keyed by dataset ID.

        Returns:
            The summary.
        """

        def lookup(dataset_id: str, _source: CatalogEntryDict) -> SourceLookup:
            """Answers what the CDAS dataset list says about one dataset."""
            dataset = cdas_datasets.get(dataset_id)
            if dataset is None:
                return SourceLookup(LookupOutcome.MISSING)
            if dataset.time_interval is None:
                return SourceLookup(LookupOutcome.UNRESOLVED)
            interval = dataset.time_interval
            return SourceLookup(LookupOutcome.FOUND, interval.start, interval.stop, interval.stop_time)

        checked_at = self.clock()
        summary = refresh_sources(self.catalog_repository, SourceType.CDAWEB, lookup, checked_at, checked_at - ONGOING_TOLERANCE)
        logger.info(
            "CDAS availability refresh: %d sources refreshed (%d ongoing), %d missing, %d without usable time range",
            summary.refreshed,
            summary.ongoing,
            summary.missing,
            summary.unresolved,
        )
        return summary
