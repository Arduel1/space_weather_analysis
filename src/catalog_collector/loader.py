import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import ValidationError
from pymongo import errors as pymongo_errors
from pymongo.database import Database

from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem
from space_weather_analysis.catalog.repository import CatalogRepository, MongoCatalogRepository
from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.models import DatasetMetadata, RawMetadataRecord
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository
from space_weather_analysis.util.naming import sanitize_collection_name

logger = logging.getLogger(__name__)


class CatalogLoader:
    """Loads the master catalog file into MongoDB.

    This class reads the generated JSON catalog and updates the MongoDB
    collection, ensuring that the central database reflects the latest
    discovery results.
    """

    HAPI_SOURCE_TYPE = "HAPI"

    def __init__(
        self,
        db: Database,
        file_path: pathlib.Path,
        metadata_path: Optional[pathlib.Path] = None,
    ):
        """Initializes the Catalog Loader.

        Args:
            db (Database): The MongoDB database instance.
            file_path (pathlib.Path): Path to the catalog JSON file.
            metadata_path (Optional[pathlib.Path]): Path to the metadata JSON file.
        """
        self.file_path = file_path
        self.metadata_path = metadata_path
        self.metadata_repository: MetadataRepository = MongoMetadataRepository(db)
        self.catalog_repository: CatalogRepository = MongoCatalogRepository(db)

    def _load_json(self) -> Optional[List[MasterCatalogItem]]:
        """Reads and parses the catalog JSON file.

        Returns:
            Optional[List[MasterCatalogItem]]: List of catalog items or None.
        """
        if not self.file_path.exists():
            logger.error(f"Catalog file not found: {self.file_path}")
            return None

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            return [{"dataset_id": k, "sources": v} for k, v in data.items()]
        except Exception as e:
            logger.error(f"Failed to load JSON catalog: {e}", exc_info=True)
            return None

    def _get_sortable_key(self, source: CatalogEntryDict) -> Tuple[Any, ...]:
        """Helper for stable sorting of sources."""
        tr = source.get("time_range", {})
        return (
            source.get("type"),
            source.get("source_id"),
            (tr.get("start_time"), tr.get("end_time")),
        )

    def load_to_db(self) -> None:
        """Upserts catalog entries into the MongoDB collection."""
        logger.info(f"--- Loading Catalog Cache from {self.file_path} to MongoDB ---")
        items = self._load_json()
        if not items:
            logger.warning("Catalog file is empty or missing. Starting with empty DB.")
            return
        logger.info(f"Found {len(items)} datasets in catalog file.")

        # We sort sources to ensure deterministic comparison/storage if needed
        for item in items:
            item["sources"].sort(key=self._get_sortable_key)

        try:
            self.catalog_repository.save_many(items)
        except pymongo_errors.BulkWriteError as bwe:
            logger.error(f"Bulk write error: {bwe.details}")
        except Exception as e:
            logger.error(f"DB Load failed: {e}", exc_info=True)

        logger.info("--- Catalog Load Finished ---")

    def load_metadata_to_db(self) -> None:
        """Loads the HAPI /info snapshot file into the metadata repository.

        The file holds a list of {dataset_id, metadata} entries or a mapping from dataset ID to
        metadata, where the metadata is the /info response of a HAPI server. Each response is
        saved as raw metadata of kind "hapi"; datasets with a HAPI source in the master catalog
        also get a normalized record. Malformed entries are logged and skipped.
        """
        if not self.metadata_path or not self.metadata_path.exists():
            logger.warning(f"Metadata file not found or not set: {self.metadata_path}")
            return

        logger.info(f"--- Loading Metadata Cache from {self.metadata_path} to MongoDB ---")
        try:
            with open(self.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not data:
                logger.warning("Metadata file is empty.")
                return

            # The snapshot does not record when each response was fetched, so the file time stands in
            retrieved_at = datetime.fromtimestamp(self.metadata_path.stat().st_mtime, tz=timezone.utc)
            hapi_servers = self._hapi_servers_by_dataset()

            raw_records: List[RawMetadataRecord] = []
            records: List[DatasetMetadata] = []
            for dataset_id, info in _snapshot_entries(data):
                raw_record, record = self._metadata_from_info(dataset_id, info, hapi_servers.get(dataset_id), retrieved_at)
                if raw_record is not None:
                    raw_records.append(raw_record)
                if record is not None:
                    records.append(record)

            self.metadata_repository.save_raw_many(raw_records)
            removed_count = self.metadata_repository.remove_outdated_records()
            if removed_count:
                logger.info(f"Removed {removed_count} metadata documents of an outdated shape.")
            self.metadata_repository.save_many(records)
            logger.info(f"Saved {len(raw_records)} raw metadata entries and {len(records)} normalized records.")

        except Exception as e:
            logger.error(f"Failed to load metadata file: {e}", exc_info=True)

        logger.info("--- Metadata Load Finished ---")

    def _hapi_servers_by_dataset(self) -> Dict[str, str]:
        """Reads the first HAPI server of each dataset from the master catalog.

        Returns:
            The base URL of the first HAPI source, keyed by dataset ID; datasets without HAPI source are left out.
        """
        hapi_servers: Dict[str, str] = {}
        for dataset_id, sources in self.catalog_repository.get_source_map(self.HAPI_SOURCE_TYPE).items():
            for source in sources:
                if source.get("source_id"):
                    hapi_servers[dataset_id] = source["source_id"]
                    break
        return hapi_servers

    def _metadata_from_info(
        self, dataset_id: str, info: Any, catalog_server_url: Optional[str], retrieved_at: datetime
    ) -> Tuple[Optional[RawMetadataRecord], Optional[DatasetMetadata]]:
        """Builds the raw metadata and, for a dataset with a HAPI source, the normalized record of one /info response.

        Args:
            dataset_id: The dataset ID.
            info: The /info response as received.
            catalog_server_url: The HAPI server of the dataset in the master catalog, None if it has no HAPI source.
            retrieved_at: When the response was read.

        Returns:
            The raw metadata, or None if the info is no mapping; the normalized record, or None if
            the dataset has no HAPI source or the info is malformed.
        """
        if not isinstance(info, dict):
            logger.warning(f"Metadata of '{dataset_id}' is not a mapping; skipped.")
            return None, None

        # The server the response was fetched from, recorded by the HAPI client, before the first catalog server
        server_url = info.get("x_server_url") or catalog_server_url
        raw_record = RawMetadataRecord(dataset_id=dataset_id, kind="hapi", payload=info, url=server_url, retrieved_at=retrieved_at)
        if catalog_server_url is None:
            return raw_record, None

        try:
            record = build_hapi_metadata(dataset_id, sanitize_collection_name(dataset_id), info, server_url, retrieved_at)
        except (ValueError, ValidationError) as e:
            logger.warning(f"Metadata of '{dataset_id}' could not be normalized; only the raw metadata is saved: {e}")
            return raw_record, None
        return raw_record, record

    def upsert_dataset(self, dataset_id: str, sources: List[CatalogEntryDict]) -> None:
        """Upserts a single dataset entry into MongoDB."""
        # Sort sources to ensure deterministic storage
        sorted_sources = sorted(sources, key=self._get_sortable_key)

        try:
            self.catalog_repository.save({"dataset_id": dataset_id, "sources": sorted_sources})
            logger.info(f"DB Saved: {dataset_id} (Sources: {len(sorted_sources)})")
        except Exception as e:
            logger.error(f"Failed to upsert dataset {dataset_id}: {e}")

    def delete_dataset(self, dataset_id: str) -> None:
        """Deletes a dataset from the MongoDB collection."""
        try:
            if self.catalog_repository.delete(dataset_id):
                logger.info(f"DB Deleted: {dataset_id}")
            else:
                logger.info(f"DB Delete skipped (not found): {dataset_id}")
        except Exception as e:
            logger.error(f"Failed to delete dataset {dataset_id}: {e}")

    def upsert_metadata(self, dataset_id: str, metadata: Dict[str, Any]) -> None:
        """Saves the metadata a discoverer found for a dataset.

        HAPI /info responses, recognized by their parameters and the server URL the HAPI client
        adds, are saved as raw metadata and as normalized record; other metadata is not stored.

        Args:
            dataset_id: The dataset ID.
            metadata: The metadata yielded by the discoverer.
        """
        if not isinstance(metadata.get("parameters"), list) or not metadata.get("x_server_url"):
            logger.debug(f"Metadata of '{dataset_id}' is no HAPI info; not stored.")
            return

        retrieved_at = datetime.now(timezone.utc)
        raw_record, record = self._metadata_from_info(dataset_id, metadata, metadata["x_server_url"], retrieved_at)
        try:
            if raw_record is not None:
                self.metadata_repository.save_raw_many([raw_record])
            if record is not None:
                self.metadata_repository.save(record)
            logger.info(f"Metadata saved: {dataset_id}")
        except Exception as e:
            logger.error(f"Failed to upsert metadata for {dataset_id}: {e}")

    def delete_metadata(self, dataset_id: str) -> None:
        """Deletes the normalized record and the raw metadata of a dataset."""
        try:
            self.metadata_repository.delete(dataset_id)
            logger.info(f"Metadata Deleted: {dataset_id}")
        except Exception as e:
            logger.error(f"Failed to delete metadata for {dataset_id}: {e}")


def _snapshot_entries(data: Any) -> Iterable[Tuple[str, Any]]:
    """Lists dataset ID and metadata of each entry of the metadata snapshot.

    Args:
        data: The parsed snapshot, a list of {dataset_id, metadata} entries or a mapping from dataset ID to metadata.

    Returns:
        Dataset ID and metadata per entry; list entries without dataset ID are logged and left out.
    """
    if isinstance(data, dict):
        return list(data.items())

    entries: List[Tuple[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        dataset_id = None
        if isinstance(item, dict):
            dataset_id = item.get("dataset_id") or item.get("id")
        if not dataset_id:
            logger.warning("Metadata entry without dataset ID skipped.")
            continue
        # An entry is either {dataset_id, metadata} or the metadata itself
        entries.append((str(dataset_id), item.get("metadata", item)))
    return entries
