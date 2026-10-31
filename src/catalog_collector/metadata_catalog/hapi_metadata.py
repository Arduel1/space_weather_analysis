import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import batched
from typing import Any

import requests
from pydantic import ValidationError

from catalog_collector.hapi_mirror.files import MIRROR_BASE_URL, MIRROR_INFOS_PATH, fetch_mirror_json_streamed, mirror_file_url
from catalog_collector.hapi_mirror.servers import describes_hapi_source, first_hapi_servers, index_mirror_servers, mirror_server_id
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.models import DatasetMetadata, RawMetadataRecord
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository, sanitize_for_bson
from space_weather_analysis.util.error_handling import DataProcessingError
from space_weather_analysis.util.naming import sanitize_collection_name

logger = logging.getLogger(__name__)

# Keys that change without the dataset changing: the time the mirror fetched the info, and the server URL the HAPI client records.
VOLATILE_INFO_KEYS = ("x_LastUpdate", "x_server_url")

# Datasets compared and written per round trip to the database.
BATCH_SIZE = 1000


@dataclass
class MirrorInfos:
    """The servers and /info responses of the mirror's full file.

    Attributes:
        server_ids_by_url: The mirror server ID, keyed by the normalized server base URL.
        listed_datasets: The dataset IDs of each mirror server, including those without /info response.
        infos: The /info response per (mirror server ID, dataset ID).
    """

    server_ids_by_url: dict[str, str]
    listed_datasets: dict[str, set[str]]
    infos: dict[tuple[str, str], dict[str, Any]]


@dataclass(frozen=True)
class RefreshCandidate:
    """A dataset of the catalog whose HAPI server the mirror maintains.

    Attributes:
        dataset_id: The dataset ID.
        server_url: The first HAPI server of the dataset in the catalog, the server its record describes.
        info: The /info response as mirrored.
    """

    dataset_id: str
    server_url: str
    info: dict[str, Any]


@dataclass
class HapiMirrorMetadataSummary:
    """The outcome of one metadata refresh from the HAPI mirror.

    Attributes:
        replaced: Datasets whose raw metadata and normalized record were rebuilt from a changed /info response.
        unchanged: Datasets whose /info response equals the stored raw metadata.
        missing: Datasets the mirror no longer lists at their server; their metadata is kept.
        unusable: Datasets listed without /info response or with one that cannot be normalized; their metadata is kept.
        other_source: Datasets whose record comes from another source kind or server and is kept.
        untouched_servers: Server URLs of the catalog that the mirror does not maintain.
    """

    replaced: int = 0
    unchanged: int = 0
    missing: int = 0
    unusable: int = 0
    other_source: int = 0
    untouched_servers: list[str] = field(default_factory=list)


class HapiMirrorMetadataRefresher:
    """Keeps the HAPI metadata of the catalog datasets current from the HAPI metadata mirror.

    Only changed /info responses are normalized again. Stored metadata is never deleted: a missing
    dataset or an unusable response leave the last good state in place.
    """

    def __init__(
        self,
        catalog_repository: CatalogRepository,
        metadata_repository: MetadataRepository,
        session: requests.Session,
        mirror_base_url: str = MIRROR_BASE_URL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initializes the refresher.

        Args:
            catalog_repository: The master catalog naming the HAPI server of each dataset.
            metadata_repository: The raw metadata and normalized records to refresh.
            session: The HTTP session for the request.
            mirror_base_url: The base URL of the mirror files.
            clock: Returns the current time in UTC.
        """
        self.catalog_repository = catalog_repository
        self.metadata_repository = metadata_repository
        self.session = session
        self.mirror_base_url = mirror_base_url
        self.clock = clock

    def refresh(self) -> HapiMirrorMetadataSummary:
        """Replaces the metadata of every catalog dataset whose mirrored /info response changed.

        Returns:
            The summary.

        Raises:
            DataRetrievalError: If the mirror file cannot be downloaded; the metadata is then left unchanged.
            DataProcessingError: If the mirror file lacks its expected structure; the metadata is then left unchanged.
        """
        mirror = parse_mirror_infos(fetch_mirror_json_streamed(self.session, self.mirror_base_url, MIRROR_INFOS_PATH))

        summary = HapiMirrorMetadataSummary()
        untouched_servers: set[str] = set()
        candidates: list[RefreshCandidate] = []
        for dataset_id, server_url in first_hapi_servers(self.catalog_repository).items():
            server_id = mirror_server_id(server_url, dataset_id, mirror.server_ids_by_url, mirror.listed_datasets)
            if server_id is None:
                untouched_servers.add(server_url)
                continue
            info = mirror.infos.get((server_id, dataset_id))
            if info is not None:
                candidates.append(RefreshCandidate(dataset_id, server_url, info))
            elif dataset_id in mirror.listed_datasets[server_id]:
                summary.unusable += 1
            else:
                summary.missing += 1

        retrieved_at = self.clock()
        for batch in batched(candidates, BATCH_SIZE):
            self._refresh_batch(batch, retrieved_at, summary)

        summary.untouched_servers = sorted(untouched_servers)
        logger.info(
            "HAPI mirror metadata refresh: %d replaced, %d unchanged, %d missing, %d unusable, %d kept from another source; "
            "servers not in the mirror: %s",
            summary.replaced,
            summary.unchanged,
            summary.missing,
            summary.unusable,
            summary.other_source,
            summary.untouched_servers,
        )
        return summary

    def _refresh_batch(self, batch: Sequence[RefreshCandidate], retrieved_at: datetime, summary: HapiMirrorMetadataSummary) -> None:
        """Compares one batch of mirrored /info responses with the stored metadata and replaces the changed ones.

        A stored record that does not match the schema counts as absent, so the mirror replaces it.

        Args:
            batch: The datasets to compare.
            retrieved_at: When the mirror file was read.
            summary: The summary to count the outcomes in, changed in place.
        """
        dataset_ids = [candidate.dataset_id for candidate in batch]
        stored_records = self.metadata_repository.get_many(dataset_ids)
        stored_raw_records = self.metadata_repository.get_raw_many(dataset_ids, "hapi")

        mirror_url = mirror_file_url(self.mirror_base_url, MIRROR_INFOS_PATH)
        new_records: list[DatasetMetadata] = []
        new_raw_records: list[RawMetadataRecord] = []
        for candidate in batch:
            stored_record = stored_records.get(candidate.dataset_id)
            if stored_record is not None and not describes_hapi_source(stored_record, candidate.server_url):
                summary.other_source += 1
                continue

            payload = sanitize_for_bson(_without_volatile_keys(candidate.info))
            stored_raw_record = stored_raw_records.get(candidate.dataset_id)
            if stored_record is not None and stored_raw_record is not None and _without_volatile_keys(stored_raw_record.payload) == payload:
                summary.unchanged += 1
                continue

            try:
                record = build_hapi_metadata(
                    candidate.dataset_id, sanitize_collection_name(candidate.dataset_id), payload, candidate.server_url, retrieved_at
                )
            except (ValueError, ValidationError) as e:
                logger.warning("Mirrored metadata of '%s' could not be normalized; the stored metadata is kept: %s", candidate.dataset_id, e)
                summary.unusable += 1
                continue
            new_records.append(record)
            new_raw_records.append(
                RawMetadataRecord(dataset_id=candidate.dataset_id, kind="hapi", payload=payload, url=mirror_url, retrieved_at=retrieved_at)
            )

        # Records first: if the raw write fails, the next refresh still sees the change and rebuilds the record
        save_with_overlays(self.metadata_repository, new_records)
        self.metadata_repository.save_raw_many(new_raw_records)
        summary.replaced += len(new_records)


def parse_mirror_infos(catalogs: Any) -> MirrorInfos:
    """Extracts servers and /info responses from the mirror's full file.

    Args:
        catalogs: The parsed catalogs-all.json: `{server_id}` mapped to its about response and its catalog entries with their info.

    Returns:
        The servers and infos.

    Raises:
        DataProcessingError: If the file lacks its expected structure.
    """
    if not isinstance(catalogs, dict):
        raise DataProcessingError("HAPI mirror full file is no JSON object")
    try:
        server_ids_by_url, listed_datasets = index_mirror_servers(catalogs)
        infos: dict[tuple[str, str], dict[str, Any]] = {}
        for server_id, server_entry in catalogs.items():
            for entry in server_entry.get("catalog", {}).get("catalog") or []:
                if isinstance(entry, dict) and "id" in entry and isinstance(entry.get("info"), dict):
                    infos[(server_id, entry["id"])] = entry["info"]
    except AttributeError as e:
        raise DataProcessingError(f"HAPI mirror full file lacks its expected structure: {e}") from e
    return MirrorInfos(server_ids_by_url, listed_datasets, infos)


def _without_volatile_keys(info: dict[str, Any]) -> dict[str, Any]:
    """Returns the /info response without the keys listed in VOLATILE_INFO_KEYS.

    Args:
        info: The /info response.

    Returns:
        A shallow copy without the volatile keys.
    """
    return {key: value for key, value in info.items() if key not in VOLATILE_INFO_KEYS}
