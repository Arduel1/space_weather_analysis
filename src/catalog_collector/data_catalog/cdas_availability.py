import logging
from collections.abc import Callable
from datetime import UTC, datetime

from catalog_collector.cdaweb.datasets import CdasDataset
from catalog_collector.data_catalog.availability_state import (
    ONGOING_TOLERANCE,
    AvailabilitySummary,
    LookupOutcome,
    SourceCandidate,
    SourceLookup,
    add_new_sources,
    refresh_sources,
)
from space_weather_analysis.catalog.models import CatalogEntryDict, SourceType
from space_weather_analysis.catalog.repository import CatalogRepository

logger = logging.getLogger(__name__)


class CdasAvailabilityRefresher:
    """Keeps the time range of the CDAWeb sources (CDAWEB) current from the CDAS dataset list.

    Sources whose dataset CDAS no longer lists are kept and only counted as missing. Datasets CDAS lists
    without CDAWEB source in the catalog get one.
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
        """Updates the availability of every CDAWEB source of the catalog from the read dataset list and adds the new datasets.

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
            return _dataset_lookup(dataset)

        checked_at = self.clock()
        ongoing_since = checked_at - ONGOING_TOLERANCE
        summary = refresh_sources(self.catalog_repository, SourceType.CDAWEB, lookup, checked_at, ongoing_since)
        candidates = [
            SourceCandidate(dataset.dataset_id, SourceType.CDAWEB, _observatory_group(dataset), _dataset_lookup(dataset))
            for dataset in cdas_datasets.values()
        ]
        add_new_sources(self.catalog_repository, candidates, _is_cdaweb_source, summary, checked_at, ongoing_since)
        logger.info(
            "CDAS availability refresh: %d sources refreshed (%d ongoing), %d missing, %d without usable time range, %d added, "
            "%d new datasets without usable time range",
            summary.refreshed,
            summary.ongoing,
            summary.missing,
            summary.unresolved,
            summary.added,
            summary.undated,
        )
        return summary


def _dataset_lookup(dataset: CdasDataset) -> SourceLookup:
    """Answers what one CDAS entry says about the time range of its dataset.

    Args:
        dataset: The CDAS entry.

    Returns:
        FOUND with the time range, or UNRESOLVED if the entry has no parsable time range.
    """
    if dataset.time_interval is None:
        return SourceLookup(LookupOutcome.UNRESOLVED)
    interval = dataset.time_interval
    return SourceLookup(LookupOutcome.FOUND, interval.start, interval.stop, interval.stop_time)


def _observatory_group(dataset: CdasDataset) -> str:
    """Reads the first observatory group of a CDAS entry, the source ID of a CDAWEB source.

    Args:
        dataset: The CDAS entry.

    Returns:
        The group name, or an empty text if the entry names none.
    """
    groups = dataset.entry.get("ObservatoryGroup")
    if isinstance(groups, list) and groups and isinstance(groups[0], str):
        return groups[0]
    return ""


def _is_cdaweb_source(source: CatalogEntryDict, _candidate: SourceCandidate) -> bool:
    """Checks whether a source of the candidate's dataset is a CDAWEB source, as each dataset has at most one."""
    return source.get("type") == SourceType.CDAWEB
