import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem, SourceType
from space_weather_analysis.catalog.repository import CatalogRepository

logger = logging.getLogger(__name__)

# Datasets updated monthly or with some lag still count as ongoing, while ended missions stop years back.
ONGOING_TOLERANCE = timedelta(days=60)


class LookupOutcome(StrEnum):
    """What a provider listing says about one catalog source."""

    FOUND = "found"
    MISSING = "missing"
    UNRESOLVED = "unresolved"
    UNTOUCHED = "untouched"


@dataclass(frozen=True)
class SourceLookup:
    """The answer of a provider listing for one catalog source.

    Attributes:
        outcome: FOUND with a time range, MISSING if the listing no longer names the dataset, UNRESOLVED if it names
            the dataset without usable time range, UNTOUCHED if the listing does not cover the source at all.
        start: The start time text as given by the provider; set for FOUND.
        stop: The stop time text as given by the provider; set for FOUND.
        stop_time: The parsed stop time in UTC; set for FOUND.
    """

    outcome: LookupOutcome
    start: str | None = None
    stop: str | None = None
    stop_time: datetime | None = None


@dataclass
class AvailabilitySummary:
    """The outcome of one availability refresh.

    Attributes:
        refreshed: Sources whose time range was set from the provider.
        ongoing: Refreshed sources that still receive new data.
        missing: Sources whose dataset the provider no longer lists; their state is kept.
        unresolved: Sources whose dataset the provider lists without usable time range; left unchanged.
        untouched_sources: Source IDs of the catalog that the provider listing does not cover; their sources are left unchanged.
        added: New sources for datasets the provider lists and the catalog did not hold yet.
        undated: Listed datasets not added, as the provider gives no usable time range for them.
    """

    refreshed: int = 0
    ongoing: int = 0
    missing: int = 0
    unresolved: int = 0
    added: int = 0
    undated: int = 0
    untouched_sources: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SourceCandidate:
    """A source that a provider listing names and that the catalog may not hold yet.

    Attributes:
        dataset_id: The dataset ID.
        source_type: The type of the source.
        source_id: The source ID the new source gets.
        lookup: What the listing says about the dataset; only FOUND adds a source.
    """

    dataset_id: str
    source_type: SourceType
    source_id: str
    lookup: SourceLookup


def add_new_sources(
    catalog_repository: CatalogRepository,
    candidates: Iterable[SourceCandidate],
    covers: Callable[[CatalogEntryDict, SourceCandidate], bool],
    summary: AvailabilitySummary,
    checked_at: datetime,
    ongoing_since: datetime,
) -> None:
    """Adds a source for every candidate that no source of its dataset covers yet and saves the changed items.

    A new source of a dataset already in the catalog is appended to its sources; the other sources stay as they are.

    Args:
        catalog_repository: The master catalog to extend.
        candidates: The sources the provider listing names.
        covers: Checks whether an existing source of the dataset already serves the candidate.
        summary: The summary of the refresh; added and undated are counted into it.
        checked_at: The time of this refresh in UTC.
        ongoing_since: The earliest stop time of a source that still counts as ongoing.
    """
    items = {item["dataset_id"]: item for item in catalog_repository.get_all()}
    changed_items: dict[str, MasterCatalogItem] = {}
    for candidate in candidates:
        item = items.setdefault(candidate.dataset_id, {"dataset_id": candidate.dataset_id, "sources": []})
        if any(covers(source, candidate) for source in item["sources"]):
            continue
        result = candidate.lookup
        if result.outcome != LookupOutcome.FOUND or not (result.start and result.stop and result.stop_time):
            summary.undated += 1
            continue
        source: CatalogEntryDict = {
            "type": candidate.source_type,
            "source_id": candidate.source_id,
            "time_range": {"start_time": result.start, "end_time": result.stop},
        }
        apply_availability(source, result.start, result.stop, ongoing=result.stop_time >= ongoing_since, checked_at=checked_at)
        item["sources"].append(source)
        changed_items[candidate.dataset_id] = item
        summary.added += 1
    catalog_repository.save_many(list(changed_items.values()))


def refresh_sources(
    catalog_repository: CatalogRepository,
    source_type: SourceType,
    lookup: Callable[[str, CatalogEntryDict], SourceLookup],
    checked_at: datetime,
    ongoing_since: datetime,
) -> AvailabilitySummary:
    """Applies a provider listing to every catalog source of one type and saves the changed items.

    Args:
        catalog_repository: The master catalog to refresh.
        source_type: The type of the sources the listing describes.
        lookup: Answers for a dataset ID and one of its sources what the listing says about it.
        checked_at: The time of this refresh in UTC.
        ongoing_since: The earliest stop time of a source that still counts as ongoing.

    Returns:
        The summary.
    """
    summary = AvailabilitySummary()
    untouched_sources: set[str] = set()
    changed_items: list[MasterCatalogItem] = []
    for item in catalog_repository.get_all():
        item_changed = False
        for source in item["sources"]:
            if source.get("type") != source_type:
                continue
            result = lookup(item["dataset_id"], source)
            if result.outcome == LookupOutcome.FOUND and result.start and result.stop and result.stop_time:
                apply_availability(source, result.start, result.stop, ongoing=result.stop_time >= ongoing_since, checked_at=checked_at)
                summary.refreshed += 1
                summary.ongoing += int(source["ongoing"])
                item_changed = True
            elif result.outcome == LookupOutcome.MISSING:
                mark_missing(source, checked_at)
                summary.missing += 1
                item_changed = True
            elif result.outcome == LookupOutcome.UNTOUCHED:
                untouched_sources.add(source["source_id"])
            else:
                summary.unresolved += 1
        if item_changed:
            changed_items.append(item)

    catalog_repository.save_many(changed_items)
    summary.untouched_sources = sorted(untouched_sources)
    return summary


def apply_availability(source: CatalogEntryDict, start: str, stop: str, *, ongoing: bool, checked_at: datetime) -> None:
    """Sets the time range of a source to the values its provider reports and clears its missing state.

    Args:
        source: The catalog source, changed in place.
        start: The start time text as given by the provider.
        stop: The stop time text as given by the provider.
        ongoing: Whether the source still receives new data.
        checked_at: The time of this refresh in UTC.
    """
    source["time_range"] = {"start_time": start, "end_time": stop}
    source["ongoing"] = ongoing
    source["availability_checked_at"] = checked_at
    source["missing_runs"] = 0
    source.pop("missing_since", None)


def mark_missing(source: CatalogEntryDict, checked_at: datetime) -> None:
    """Counts one more run that missed the dataset of a source and keeps everything else.

    Args:
        source: The catalog source, changed in place.
        checked_at: The time of this run in UTC.
    """
    source["missing_runs"] = source.get("missing_runs", 0) + 1
    if "missing_since" not in source:
        source["missing_since"] = checked_at
