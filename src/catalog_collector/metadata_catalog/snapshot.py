import json
import logging
import pathlib
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from catalog_collector.hapi_mirror.servers import describes_hapi_source, first_hapi_servers, normalize_server_url
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSourceKind, RawMetadataRecord
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.util.error_handling import CatalogError
from space_weather_analysis.util.naming import sanitize_collection_name

logger = logging.getLogger(__name__)

# The key under which the HAPI client records the server a /info response came from.
SERVER_URL_KEY = "x_server_url"


class MetadataSnapshotLoader:
    """Loads the HAPI /info snapshot file and the metadata found by discovery into the metadata repository.

    The snapshot only fills in datasets without stored HAPI metadata, as everything stored since is newer.
    Records describe the first HAPI server of the dataset in the catalog, the rule every writer of HAPI records
    follows, and a stored record of another source or server is never replaced.
    """

    def __init__(
        self,
        catalog_repository: CatalogRepository,
        metadata_repository: MetadataRepository,
        file_path: pathlib.Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initializes the loader.

        Args:
            catalog_repository: The master catalog naming the HAPI server of each dataset.
            metadata_repository: The repository to load into.
            file_path: Path to the metadata JSON file.
            clock: Returns the current time in UTC.
        """
        self.catalog_repository = catalog_repository
        self.metadata_repository = metadata_repository
        self.file_path = file_path
        self.clock = clock

    def load_metadata_to_db(self) -> None:
        """Saves the snapshot entries of datasets without stored HAPI metadata as raw metadata and normalized records.

        The file holds a list of {dataset_id, metadata} entries or a mapping from dataset ID to metadata, where the
        metadata is the /info response of a HAPI server. Datasets with a HAPI source in the catalog also get a
        normalized record. Malformed entries are logged and skipped.

        Raises:
            CatalogError: If the file is missing or no JSON.
            pymongo.errors.PyMongoError: If the metadata cannot be written.
        """
        try:
            data: Any = json.loads(self.file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise CatalogError(f"Metadata snapshot file {self.file_path} cannot be read: {e}") from e

        # The snapshot does not record when each response was fetched, so the file time stands in
        retrieved_at = datetime.fromtimestamp(self.file_path.stat().st_mtime, tz=UTC)
        snapshot_url = self.file_path.resolve().as_uri()
        record_servers = first_hapi_servers(self.catalog_repository)
        stored_dataset_ids = self.metadata_repository.get_raw_dataset_ids(MetadataSourceKind.HAPI)

        new_entries = [(dataset_id, info) for dataset_id, info in _snapshot_entries(data) if dataset_id not in stored_dataset_ids]
        stored_records = self.metadata_repository.get_many([dataset_id for dataset_id, _ in new_entries])
        raw_records: list[RawMetadataRecord] = []
        records: list[DatasetMetadata] = []
        for dataset_id, info in new_entries:
            if not isinstance(info, dict):
                logger.warning("Metadata of '%s' is not a mapping; skipped.", dataset_id)
                continue
            raw_records.append(
                RawMetadataRecord(dataset_id=dataset_id, kind=MetadataSourceKind.HAPI, payload=info, url=snapshot_url, retrieved_at=retrieved_at)
            )
            record = self._record(dataset_id, info, record_servers.get(dataset_id), stored_records.get(dataset_id), retrieved_at)
            if record is not None:
                records.append(record)

        self.metadata_repository.save_raw_many(raw_records)
        save_with_overlays(self.metadata_repository, records)
        logger.info(
            "Metadata snapshot %s: saved %d raw entries and %d records; %d datasets already had stored HAPI metadata.",
            self.file_path,
            len(raw_records),
            len(records),
            len(stored_dataset_ids),
        )

    def upsert_metadata(self, dataset_id: str, info: dict[str, Any]) -> None:
        """Saves the /info response that discovery read from one HAPI server.

        The response is stored only if it comes from the dataset's first HAPI server in the catalog, the server
        its record describes; responses of further servers would replace that server's metadata.

        Args:
            dataset_id: The dataset ID.
            info: The /info response, with the server URL in 'x_server_url'.

        Raises:
            pymongo.errors.PyMongoError: If the metadata cannot be written.
        """
        server_url = info.get(SERVER_URL_KEY)
        record_server = first_hapi_servers(self.catalog_repository).get(dataset_id)
        if not server_url or record_server is None or normalize_server_url(record_server) != normalize_server_url(server_url):
            logger.debug("Metadata of '%s' from %s does not describe the dataset's first HAPI server; not stored.", dataset_id, server_url)
            return
        retrieved_at = self.clock()
        self.metadata_repository.save_raw_many(
            [RawMetadataRecord(dataset_id=dataset_id, kind=MetadataSourceKind.HAPI, payload=info, url=server_url, retrieved_at=retrieved_at)]
        )
        record = self._record(dataset_id, info, record_server, self.metadata_repository.get(dataset_id), retrieved_at)
        if record is not None:
            save_with_overlays(self.metadata_repository, [record])

    @staticmethod
    def _record(
        dataset_id: str, info: dict[str, Any], record_server: str | None, stored_record: DatasetMetadata | None, retrieved_at: datetime
    ) -> DatasetMetadata | None:
        """Builds the normalized record of one /info response for the dataset's first HAPI server.

        Args:
            dataset_id: The dataset ID.
            info: The /info response.
            record_server: The first HAPI server of the dataset in the catalog; None if the dataset has no HAPI source.
            stored_record: The stored record of the dataset, if any.
            retrieved_at: When the response was read.

        Returns:
            The record, or None if the dataset has no HAPI source, a stored record of another source or server ranks
            above it, or the response cannot be normalized.
        """
        if record_server is None:
            return None
        if stored_record is not None and not describes_hapi_source(stored_record, record_server):
            logger.info("Record of '%s' comes from another source or server and is kept.", dataset_id)
            return None
        try:
            return build_hapi_metadata(dataset_id, sanitize_collection_name(dataset_id), info, record_server, retrieved_at)
        except (ValueError, ValidationError) as e:
            logger.warning("Metadata of '%s' could not be normalized; only the raw metadata is saved: %s", dataset_id, e)
            return None


def _snapshot_entries(data: Any) -> Iterable[tuple[str, Any]]:
    """Lists dataset ID and metadata of each entry of the metadata snapshot.

    Args:
        data: The parsed snapshot, a list of {dataset_id, metadata} entries or a mapping from dataset ID to metadata.

    Returns:
        Dataset ID and metadata per entry; list entries without dataset ID are logged and left out.
    """
    if isinstance(data, dict):
        return list(data.items())

    entries: list[tuple[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        dataset_id = (item.get("dataset_id") or item.get("id")) if isinstance(item, dict) else None
        if not dataset_id:
            logger.warning("Metadata entry without dataset ID skipped.")
            continue
        # An entry is either {dataset_id, metadata} or the metadata itself
        entries.append((str(dataset_id), item.get("metadata", item)))
    return entries
