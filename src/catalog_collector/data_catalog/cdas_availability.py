import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import requests

from catalog_collector.cdaweb.datasets import CDAS_DATASETS_URL, fetch_cdas_datasets
from catalog_collector.data_catalog.availability_state import ONGOING_TOLERANCE, apply_availability, format_utc, mark_missing
from space_weather_analysis.catalog.models import MasterCatalogItem, SourceType
from space_weather_analysis.catalog.repository import CatalogRepository

logger = logging.getLogger(__name__)


@dataclass
class CdasAvailabilitySummary:
    """The outcome of one availability refresh from CDAS.

    Attributes:
        refreshed: Sources whose time range was set from CDAS.
        ongoing: Refreshed sources that still receive new data.
        missing: Sources whose dataset CDAS no longer lists; their state is kept.
        unresolved: Sources whose dataset CDAS lists without parsable time range; left unchanged.
    """

    refreshed: int = 0
    ongoing: int = 0
    missing: int = 0
    unresolved: int = 0


class CdasAvailabilityRefresher:
    """Keeps the time range of the CDAWeb sources ingested through SunPy Fido current from the CDAS dataset list.

    Sources whose dataset CDAS no longer lists are kept and only counted as missing.
    """

    def __init__(self, catalog_repository: CatalogRepository, session: requests.Session, url: str = CDAS_DATASETS_URL) -> None:
        """Initializes the refresher.

        Args:
            catalog_repository: The master catalog to refresh.
            session: The HTTP session for the request.
            url: The location of the CDAS dataset list.
        """
        self.catalog_repository = catalog_repository
        self.session = session
        self.url = url

    def refresh(self) -> CdasAvailabilitySummary:
        """Updates the availability of every FIDO_CDAWEB source of the catalog.

        Returns:
            The summary.

        Raises:
            DataRetrievalError, DataProcessingError: If the dataset list cannot be read; the catalog is then left unchanged.
        """
        datasets = fetch_cdas_datasets(self.session, self.url)
        fetched_at = datetime.now(UTC)
        checked_at = format_utc(fetched_at)
        ongoing_since = fetched_at - ONGOING_TOLERANCE
        summary = CdasAvailabilitySummary()
        changed_items: list[MasterCatalogItem] = []

        for item in self.catalog_repository.get_all():
            item_changed = False
            for source in item["sources"]:
                if source.get("type") != SourceType.FIDO_CDAWEB:
                    continue
                dataset = datasets.get(item["dataset_id"])
                if dataset is None:
                    mark_missing(source, checked_at)
                    summary.missing += 1
                elif dataset.time_interval is None:
                    summary.unresolved += 1
                    continue
                else:
                    interval = dataset.time_interval
                    apply_availability(source, interval.start, interval.stop, interval.stop_time >= ongoing_since, checked_at)
                    summary.refreshed += 1
                    summary.ongoing += int(source["ongoing"])
                item_changed = True
            if item_changed:
                changed_items.append(item)

        self.catalog_repository.save_many(changed_items)
        logger.info(
            "CDAS availability refresh: %d sources refreshed (%d ongoing), %d missing, %d without usable time range",
            summary.refreshed,
            summary.ongoing,
            summary.missing,
            summary.unresolved,
        )
        return summary
