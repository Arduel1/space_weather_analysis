import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests

from catalog_collector.data_catalog.availability_state import (
    ONGOING_TOLERANCE,
    AvailabilitySummary,
    LookupOutcome,
    SourceLookup,
    refresh_sources,
)
from catalog_collector.hapi_mirror.files import MIRROR_BASE_URL, MIRROR_CATALOGS_PATH, MIRROR_DATASET_TABLE_PATH, fetch_mirror_json
from catalog_collector.hapi_mirror.servers import index_mirror_servers, mirror_server_id
from space_weather_analysis.catalog.models import CatalogEntryDict, SourceType
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.util.error_handling import DataProcessingError
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatasetAvailability:
    """The time range of one dataset as the mirror reports it.

    Attributes:
        start: The start time text as given by the mirror.
        stop: The stop time text as given by the mirror.
        stop_time: The parsed stop time in UTC.
    """

    start: str
    stop: str
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
    server_ids_by_url: dict[str, str]
    listed_datasets: dict[str, set[str]]
    availability: dict[tuple[str, str], DatasetAvailability | None]

    def lookup(self, dataset_id: str, source: CatalogEntryDict) -> SourceLookup:
        """Answers what the mirror says about one HAPI source of the catalog.

        Args:
            dataset_id: The dataset ID.
            source: The HAPI source.

        Returns:
            FOUND with the time range, UNRESOLVED if the mirror lists the dataset without usable dates, MISSING if the
            mirrored server no longer lists it, or UNTOUCHED if the mirror does not maintain the server.
        """
        server_id = mirror_server_id(source["source_id"], dataset_id, self.server_ids_by_url, self.listed_datasets)
        if server_id is None:
            return SourceLookup(LookupOutcome.UNTOUCHED)
        if (server_id, dataset_id) in self.availability:
            availability = self.availability[(server_id, dataset_id)]
            if availability is None:
                return SourceLookup(LookupOutcome.UNRESOLVED)
            return SourceLookup(LookupOutcome.FOUND, availability.start, availability.stop, availability.stop_time)
        # Listed in the /catalog response, but the mirror has no /info dates for it
        if dataset_id in self.listed_datasets[server_id]:
            return SourceLookup(LookupOutcome.UNRESOLVED)
        return SourceLookup(LookupOutcome.MISSING)


class HapiMirrorAvailabilityRefresher:
    """Keeps the time range of the HAPI sources in the master catalog current from the HAPI metadata mirror.

    Sources whose dataset is missing in the mirror are kept and only counted as missing, as servers
    and the mirror itself are unreachable at times.
    """

    def __init__(
        self,
        catalog_repository: CatalogRepository,
        session: requests.Session,
        mirror_base_url: str = MIRROR_BASE_URL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initializes the refresher.

        Args:
            catalog_repository: The master catalog to refresh.
            session: The HTTP session for the requests.
            mirror_base_url: The base URL of the mirror files.
            clock: Returns the current time in UTC.
        """
        self.catalog_repository = catalog_repository
        self.session = session
        self.mirror_base_url = mirror_base_url
        self.clock = clock

    def refresh(self) -> AvailabilitySummary:
        """Updates the availability of every HAPI source of a server the mirror maintains.

        Returns:
            The summary; untouched_sources names the catalog servers the mirror does not maintain.

        Raises:
            DataRetrievalError: If a mirror file cannot be downloaded; the catalog is then left unchanged.
            DataProcessingError: If a mirror file lacks its expected structure; the catalog is then left unchanged.
        """
        catalogs = fetch_mirror_json(self.session, self.mirror_base_url, MIRROR_CATALOGS_PATH)
        dataset_table = fetch_mirror_json(self.session, self.mirror_base_url, MIRROR_DATASET_TABLE_PATH)
        mirror = parse_mirror(catalogs, dataset_table, self.clock())

        summary = refresh_sources(self.catalog_repository, SourceType.HAPI, mirror.lookup, self.clock(), mirror.created_at - ONGOING_TOLERANCE)
        logger.info(
            "HAPI mirror availability refresh: %d sources refreshed (%d ongoing), %d missing, %d without usable dates; servers not in the mirror: %s",
            summary.refreshed,
            summary.ongoing,
            summary.missing,
            summary.unresolved,
            summary.untouched_sources,
        )
        return summary


def parse_mirror(catalogs: Any, dataset_table: Any, now: datetime) -> MirrorSnapshot:
    """Extracts server URLs, dataset lists and time ranges from the mirror files.

    Args:
        catalogs: The parsed catalogs.json: `{server_id}` mapped to its about and catalog responses.
        dataset_table: The parsed dataset table with metadata, header and body rows in header order.
        now: The current time, which stands in for a missing creation date of the table.

    Returns:
        The snapshot.

    Raises:
        DataProcessingError: If a file lacks its expected structure.
    """
    if not isinstance(catalogs, dict) or not isinstance(dataset_table, dict):
        raise DataProcessingError("HAPI mirror catalogs or dataset table is no JSON object")
    try:
        server_ids_by_url, listed_datasets = index_mirror_servers(catalogs)
        column_index = {name: index for index, name in enumerate(dataset_table["header"])}
        availability: dict[tuple[str, str], DatasetAvailability | None] = {}
        for row in dataset_table["body"]:
            server_id = row[column_index["server"]]
            dataset_id = row[column_index["dataset"]]
            availability[(server_id, dataset_id)] = _row_availability(row, column_index)
            listed_datasets.setdefault(server_id, set()).add(dataset_id)
        creation_date = dataset_table.get("metadata", {}).get("creationDate")
    except (KeyError, TypeError, AttributeError, IndexError) as e:
        raise DataProcessingError(f"HAPI mirror catalogs or dataset table lacks its expected structure: {e}") from e
    return MirrorSnapshot(_parse_mirror_time(creation_date) or now, server_ids_by_url, listed_datasets, availability)


def _row_availability(row: list[Any], column_index: dict[str, int]) -> DatasetAvailability | None:
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
    return DatasetAvailability(start=start[0], stop=stop[0], stop_time=stop[1])


def _first_parsable_time(row: list[Any], column_index: dict[str, int], columns: tuple[str, ...]) -> tuple[str, datetime] | None:
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


def _parse_mirror_time(value: Any) -> datetime | None:
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
