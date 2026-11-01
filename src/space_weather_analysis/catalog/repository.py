from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from pymongo import ASCENDING, ReplaceOne
from pymongo.database import Database

from space_weather_analysis.catalog.models import CatalogEntryDict, CatalogMap, MasterCatalogItem
from space_weather_analysis.database.bulk import bulk_write_in_batches
from space_weather_analysis.util.time_utils import ensure_aware

# Source fields holding times, which a MongoDB client without tz_aware returns without time zone.
SOURCE_TIME_FIELDS = ("availability_checked_at", "missing_since")


class CatalogRepository(ABC):
    """Stores the master catalog: the sources each dataset can be ingested from.

    Collector, pipelines and frontend read and write the master catalog only through this interface.
    """

    @abstractmethod
    def save(self, item: MasterCatalogItem) -> None:
        """Inserts or replaces the catalog item of one dataset.

        Args:
            item: The dataset ID with its sources.
        """

    @abstractmethod
    def save_many(self, items: Sequence[MasterCatalogItem]) -> None:
        """Inserts or replaces the catalog items of many datasets.

        Args:
            items: The catalog items, at most one per dataset ID.
        """

    @abstractmethod
    def get(self, dataset_id: str) -> MasterCatalogItem | None:
        """Reads the catalog item of one dataset.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The catalog item, or None if the dataset is not in the catalog.
        """

    @abstractmethod
    def get_sources(self, dataset_id: str, source_type: str | None = None) -> list[CatalogEntryDict]:
        """Reads the sources of one dataset in their stored order.

        Args:
            dataset_id: The dataset ID.
            source_type: The source type to keep, such as 'HAPI'; None keeps all sources.

        Returns:
            The sources; empty if the dataset is not in the catalog or has no source of the type.
        """

    @abstractmethod
    def get_all(self) -> list[MasterCatalogItem]:
        """Reads every catalog item.

        Returns:
            The catalog items, sorted by dataset ID.
        """

    @abstractmethod
    def get_source_map(self, source_type: str) -> CatalogMap:
        """Reads the sources of one type for every dataset that has one.

        Args:
            source_type: The source type, such as 'HAPI'.

        Returns:
            The sources of that type in their stored order, keyed by dataset ID;
            datasets without such a source are left out.
        """

    @abstractmethod
    def delete(self, dataset_id: str) -> bool:
        """Removes the catalog item of one dataset.

        Args:
            dataset_id: The dataset ID.

        Returns:
            True if an item was removed, False if the dataset was not in the catalog.
        """


class MongoCatalogRepository(CatalogRepository):
    """Catalog repository on MongoDB.

    Each dataset is one document {dataset_id, sources} in 'master_catalog', unique by `dataset_id`.
    """

    COLLECTION = "master_catalog"
    BATCH_SIZE = 1000

    def __init__(self, db: Database) -> None:
        """Initializes the repository; the index is created before the first write.

        Args:
            db: The MongoDB database instance.
        """
        self.collection = db[self.COLLECTION]
        self._indexes_ensured = False

    def _ensure_indexes(self) -> None:
        """Creates the unique index on the dataset ID, once per instance."""
        if self._indexes_ensured:
            return
        self.collection.create_index("dataset_id", unique=True)
        self._indexes_ensured = True

    def save(self, item: MasterCatalogItem) -> None:
        """Inserts or replaces the catalog item of one dataset.

        Args:
            item: The dataset ID with its sources.
        """
        self.save_many([item])

    def save_many(self, items: Sequence[MasterCatalogItem]) -> None:
        """Inserts or replaces the catalog items of many datasets in batches of BATCH_SIZE.

        Args:
            items: The catalog items, at most one per dataset ID.

        Raises:
            pymongo.errors.BulkWriteError: If a batch fails to write.
        """
        self._ensure_indexes()
        operations = []
        for item in items:
            document = {"dataset_id": item["dataset_id"], "sources": item["sources"]}
            operations.append(ReplaceOne({"dataset_id": item["dataset_id"]}, document, upsert=True))
        bulk_write_in_batches(self.collection, operations, self.BATCH_SIZE)

    def get(self, dataset_id: str) -> MasterCatalogItem | None:
        """Reads the catalog item of one dataset.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The catalog item, or None if the dataset is not in the catalog.
        """
        document = self.collection.find_one({"dataset_id": dataset_id}, {"_id": 0})
        return _catalog_item(document) if document else None

    def get_sources(self, dataset_id: str, source_type: str | None = None) -> list[CatalogEntryDict]:
        """Reads the sources of one dataset in their stored order.

        Args:
            dataset_id: The dataset ID.
            source_type: The source type to keep, such as 'HAPI'; None keeps all sources.

        Returns:
            The sources; empty if the dataset is not in the catalog or has no source of the type.
        """
        item = self.get(dataset_id)
        if item is None:
            return []
        if source_type is None:
            return item["sources"]
        return _sources_of_type(item["sources"], source_type)

    def get_all(self) -> list[MasterCatalogItem]:
        """Reads every catalog item.

        Returns:
            The catalog items, sorted by dataset ID.
        """
        documents = self.collection.find({}, {"_id": 0}).sort("dataset_id", ASCENDING)
        return [_catalog_item(document) for document in documents]

    def get_source_map(self, source_type: str) -> CatalogMap:
        """Reads the sources of one type for every dataset that has one.

        Args:
            source_type: The source type, such as 'HAPI'.

        Returns:
            The sources of that type in their stored order, keyed by dataset ID;
            datasets without such a source are left out.
        """
        source_map: CatalogMap = {}
        for document in self.collection.find({"sources.type": source_type}, {"_id": 0}):
            item = _catalog_item(document)
            source_map[item["dataset_id"]] = _sources_of_type(item["sources"], source_type)
        return source_map

    def delete(self, dataset_id: str) -> bool:
        """Removes the catalog item of one dataset.

        Args:
            dataset_id: The dataset ID.

        Returns:
            True if an item was removed, False if the dataset was not in the catalog.
        """
        result = self.collection.delete_one({"dataset_id": dataset_id})
        return result.deleted_count > 0


def _catalog_item(document: Mapping[str, Any]) -> MasterCatalogItem:
    """Builds the catalog item of a stored document, with an empty source list if the document has none.

    Args:
        document: The stored document without `_id`.

    Returns:
        The catalog item, with the source times read as UTC.
    """
    sources = cast(list[CatalogEntryDict], document.get("sources") or [])
    for source in sources:
        for field in SOURCE_TIME_FIELDS:
            if isinstance(source.get(field), datetime):
                source[field] = ensure_aware(source[field])  # type: ignore[literal-required]
    return {"dataset_id": document["dataset_id"], "sources": sources}


def _sources_of_type(sources: list[CatalogEntryDict], source_type: str) -> list[CatalogEntryDict]:
    """Keeps the sources of one type in their order.

    Args:
        sources: The sources of a dataset.
        source_type: The source type to keep.

    Returns:
        The sources of that type.
    """
    return [source for source in sources if source.get("type") == source_type]
