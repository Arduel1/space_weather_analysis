import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from pydantic import ValidationError

from catalog_collector.data_catalog.availability_state import ONGOING_TOLERANCE, apply_availability, format_utc, mark_missing
from catalog_collector.hapi_mirror.servers import (
    MIRROR_BASE_URL,
    describes_hapi_source,
    index_mirror_servers,
    mirror_server_id,
)
from space_weather_analysis.catalog.models import MasterCatalogItem, SourceType
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.metadata.overlays import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)


# Per server: its base URL in about.x_url and the dataset IDs of its /catalog response.
MIRROR_CATALOGS_PATH = "catalogs.json"

# One row per dataset of every server, with the startDate and stopDate of its /info response.
MIRROR_DATASET_TABLE_PATH = "table/hapi.all.datasets.json"

# Connect and read timeout in seconds; the dataset table has about 25 MB.
MIRROR_TIMEOUT = (10, 300)


@dataclass(frozen=True)
class DatasetAvailability:
    """The time range of one dataset as the mirror reports it.

    Attributes:
        start: The start time text as given by the mirror.
        stop: The stop time text as given by the mirror.
        start_time: The parsed start time in UTC.
        stop_time: The parsed stop time in UTC.
    """

    start: str
    stop: str
    start_time: datetime
    stop_time: datetime


@dataclass
class MirrorSnapshot:
    """The parts of the mirror files the availability refresh needs.

    Attributes:
        created_at: When the mirror generated the dataset table.
        server_ids_by_url: The mirror server ID, keyed by the normalized server base URL.
        listed_datasets: The dataset IDs of each mirror server, from its /catalog response and the dataset table.
        availability: The time range per (mirror server ID, dataset ID); None if the row has no parsable dates.
    """

    created_at: datetime
    server_ids_by_url: Dict[str, str]
    listed_datasets: Dict[str, Set[str]]
    availability: Dict[Tuple[str, str], Optional[DatasetAvailability]]


@dataclass
class AvailabilitySummary:
    """The outcome of one availability refresh.

    Attributes:
        refreshed: Sources whose time range was set from the mirror.
        ongoing: Refreshed sources that still receive new data.
        missing: Sources whose dataset the mirror no longer lists at their server.
        unresolved: Sources whose dataset the mirror lists without parsable dates; left unchanged.
        untouched_servers: Server URLs of the catalog that the mirror does not maintain; their sources are left unchanged.
    """

    refreshed: int = 0
    ongoing: int = 0
    missing: int = 0
    unresolved: int = 0
    untouched_servers: List[str] = field(default_factory=list)


class AvailabilityRefresher:
    """Keeps the time range of the HAPI sources in the master catalog current from the HAPI metadata mirror.

    Sources whose dataset is missing in the mirror are kept and only counted as missing, as servers
    and the mirror itself are unreachable at times.
    """

    def __init__(
        self, catalog_repository: CatalogRepository, metadata_repository: MetadataRepository, mirror_base_url: str = MIRROR_BASE_URL
    ) -> None:
        """Initializes the refresher.

        Args:
            catalog_repository: The master catalog to refresh.
            metadata_repository: The metadata records whose coverage follows the refreshed time ranges.
            mirror_base_url: The base URL of the mirror files.
        """
        self.catalog_repository = catalog_repository
        self.metadata_repository = metadata_repository
        self.mirror_base_url = mirror_base_url.rstrip("/")
        self.session = requests.Session()

    def refresh(self) -> Optional[AvailabilitySummary]:
        """Updates the availability of every HAPI source of a server the mirror maintains.

        Returns:
            The summary, or None if the mirror could not be read and nothing was changed.
        """
        mirror = self._fetch_mirror()
        if mirror is None:
            return None

        checked_at = format_utc(datetime.now(timezone.utc))
        ongoing_since = mirror.created_at - ONGOING_TOLERANCE
        summary = AvailabilitySummary()
        untouched_servers: Set[str] = set()
        changed_items: List[MasterCatalogItem] = []

        for item in self.catalog_repository.get_all():
            item_changed = False
            for source in item["sources"]:
                if source.get("type") != SourceType.HAPI:
                    continue
                server_id = mirror_server_id(source["source_id"], item["dataset_id"], mirror.server_ids_by_url, mirror.listed_datasets)
                if server_id is None:
                    untouched_servers.add(source["source_id"])
                    continue

                availability_key = (server_id, item["dataset_id"])
                if availability_key in mirror.availability:
                    availability = mirror.availability[availability_key]
                    if availability is None:
                        summary.unresolved += 1
                        continue
                    apply_availability(source, availability.start, availability.stop, availability.stop_time >= ongoing_since, checked_at)
                    summary.refreshed += 1
                    summary.ongoing += int(source["ongoing"])
                    self._sync_metadata_coverage(item["dataset_id"], source["source_id"], availability)
                elif item["dataset_id"] in mirror.listed_datasets[server_id]:
                    # Listed in the /catalog response, but the mirror has no /info dates for it
                    summary.unresolved += 1
                    continue
                else:
                    mark_missing(source, checked_at)
                    summary.missing += 1
                item_changed = True
            if item_changed:
                changed_items.append(item)

        self.catalog_repository.save_many(changed_items)
        summary.untouched_servers = sorted(untouched_servers)
        logger.info(
            f"Availability refresh: {summary.refreshed} sources refreshed ({summary.ongoing} ongoing), {summary.missing} missing, "
            f"{summary.unresolved} without usable dates; servers not in the mirror: {summary.untouched_servers}"
        )
        return summary

    def _fetch_mirror(self) -> Optional[MirrorSnapshot]:
        """Downloads and parses the mirror files.

        Returns:
            The snapshot, or None if a file could not be downloaded or parsed.
        """
        try:
            catalogs = self._fetch_json(MIRROR_CATALOGS_PATH)
            dataset_table = self._fetch_json(MIRROR_DATASET_TABLE_PATH)
            return _parse_mirror(catalogs, dataset_table)
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError, IndexError) as e:
            logger.warning(f"HAPI metadata mirror at {self.mirror_base_url} could not be read; availability left unchanged: {e}")
            return None

    def _fetch_json(self, path: str) -> Any:
        """Downloads one mirror file and parses it as JSON.

        Args:
            path: The path of the file below the mirror base URL.

        Returns:
            The parsed JSON content.

        Raises:
            requests.RequestException: If the download fails or the status is not 2xx.
            ValueError: If the content is no JSON.
        """
        response = self.session.get(f"{self.mirror_base_url}/{path}", timeout=MIRROR_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def _sync_metadata_coverage(self, dataset_id: str, server_url: str, availability: DatasetAvailability) -> None:
        """Sets the coverage of the metadata record to the refreshed time range if the record describes this source.

        Args:
            dataset_id: The dataset ID.
            server_url: The base URL of the refreshed HAPI source.
            availability: The refreshed time range.
        """
        try:
            record = self.metadata_repository.get(dataset_id)
        except ValidationError as e:
            logger.warning(f"Metadata record of '{dataset_id}' is unreadable; coverage not updated: {e}")
            return
        if record is None or not describes_hapi_source(record, server_url):
            return
        if (record.time.coverage_start, record.time.coverage_stop) == (availability.start_time, availability.stop_time):
            return
        record.time.coverage_start = availability.start_time
        record.time.coverage_stop = availability.stop_time
        save_with_overlays(self.metadata_repository, [record])


def _parse_mirror(catalogs: Dict[str, Any], dataset_table: Dict[str, Any]) -> MirrorSnapshot:
    """Extracts server URLs, dataset lists and time ranges from the mirror files.

    Args:
        catalogs: The parsed catalogs.json: `{server_id}` mapped to its about and catalog responses.
        dataset_table: The parsed dataset table with metadata, header and body rows in header order.

    Returns:
        The snapshot.

    Raises:
        KeyError, TypeError, AttributeError, IndexError: If a file lacks its expected structure.
    """
    server_ids_by_url, listed_datasets = index_mirror_servers(catalogs)

    column_index = {name: index for index, name in enumerate(dataset_table["header"])}
    availability: Dict[Tuple[str, str], Optional[DatasetAvailability]] = {}
    for row in dataset_table["body"]:
        server_id = row[column_index["server"]]
        dataset_id = row[column_index["dataset"]]
        availability[(server_id, dataset_id)] = _row_availability(row, column_index)
        listed_datasets.setdefault(server_id, set()).add(dataset_id)

    # Without a creation date the mirror counts as generated now
    created_at = _parse_mirror_time(dataset_table["metadata"].get("creationDate")) or datetime.now(timezone.utc)
    return MirrorSnapshot(created_at, server_ids_by_url, listed_datasets, availability)


def _row_availability(row: List[Any], column_index: Dict[str, int]) -> Optional[DatasetAvailability]:
    """Reads the time range of one dataset table row.

    The /info dates are tried first; the `x_` columns hold the mirror's normalized form of the same
    dates, which also parses when the server wrote a day-of-year date.

    Args:
        row: The row values in header order.
        column_index: The position of each column, keyed by column name.

    Returns:
        The time range, or None if start or stop is not parsable.
    """
    start = _first_parsable_time(row, column_index, ("startDate", "x_startDate"))
    stop = _first_parsable_time(row, column_index, ("stopDate", "x_stopDate"))
    if start is None or stop is None:
        return None
    return DatasetAvailability(start=start[0], stop=stop[0], start_time=start[1], stop_time=stop[1])


def _first_parsable_time(row: List[Any], column_index: Dict[str, int], columns: Tuple[str, ...]) -> Optional[Tuple[str, datetime]]:
    """Returns text and parsed time of the first of the columns holding a parsable time.

    Args:
        row: The row values in header order.
        column_index: The position of each column, keyed by column name.
        columns: The column names to try in order.

    Returns:
        The time text and the parsed UTC time, or None if no column holds a parsable time.
    """
    for column in columns:
        index = column_index.get(column)
        value = row[index] if index is not None and index < len(row) else None
        parsed = _parse_mirror_time(value)
        if parsed is not None and isinstance(value, str):
            return value, parsed
    return None


def _parse_mirror_time(value: Any) -> Optional[datetime]:
    """Parses an ISO 8601 time of the mirror without logging failures, as empty and odd values are common there.

    Args:
        value: The value of a time column.

    Returns:
        The time in UTC, or None if the value is no parsable time text.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return ensure_aware(datetime.fromisoformat(value))
    except ValueError:
        return None
