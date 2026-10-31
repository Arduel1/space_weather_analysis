import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import numpy as np
from bson import encode
from pydantic import ValidationError
from pymongo import ASCENDING, ReplaceOne
from pymongo.database import Database

from space_weather_analysis.database.bulk import bulk_write_in_batches
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSourceKind, RawMetadataRecord

logger = logging.getLogger(__name__)


def sanitize_for_bson(value: Any) -> Any:
    """Recursively converts a value into types that BSON can store.

    Dictionaries and lists are converted element by element, NumPy arrays become
    lists, NumPy scalars become Python numbers, NaN and infinite floats become None,
    bytes are decoded as UTF-8 and other objects become their text. Dictionary keys become strings.

    Args:
        value: The value to sanitize.

    Returns:
        A BSON-compatible value (dict, list, str, int, float, bool, datetime or None).
    """
    if isinstance(value, dict):
        return {str(key): sanitize_for_bson(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [sanitize_for_bson(item) for item in value]

    if isinstance(value, np.ndarray):
        return [sanitize_for_bson(item) for item in value.tolist()]

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, np.integer):
        return int(value)

    # MongoDB uses null for missing values
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")

    if isinstance(value, np.generic):
        return sanitize_for_bson(value.item())

    if value is None or isinstance(value, (str, int, datetime)):
        return value

    # Objects without BSON type are kept as their text
    return str(value)


class MetadataRepository(ABC):
    """Stores the normalized metadata record and the raw provider metadata of each dataset.

    Collector, pipelines and frontend read and write dataset metadata only through this interface.
    """

    @abstractmethod
    def save(self, record: DatasetMetadata) -> None:
        """Inserts or replaces the record of one dataset.

        Args:
            record: The normalized metadata record.
        """

    @abstractmethod
    def save_many(self, records: Sequence[DatasetMetadata]) -> None:
        """Inserts or replaces the records of many datasets.

        Args:
            records: The normalized metadata records.
        """

    @abstractmethod
    def get(self, dataset_id: str) -> DatasetMetadata | None:
        """Reads the record of one dataset.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The record, or None if the dataset has none.
        """

    @abstractmethod
    def get_many(self, dataset_ids: Sequence[str]) -> dict[str, DatasetMetadata]:
        """Reads the records of many datasets; a record that does not match the schema is logged and left out.

        Args:
            dataset_ids: The dataset IDs.

        Returns:
            The readable records keyed by dataset ID; datasets without readable record are left out.
        """

    @abstractmethod
    def find_by_collection(self, collection: str) -> list[DatasetMetadata]:
        """Reads the records of the datasets stored in a MongoDB collection.

        Args:
            collection: The name of the collection holding the data documents.

        Returns:
            The records, sorted by dataset ID; empty if none matches.
        """

    @abstractmethod
    def save_raw(self, dataset_id: str, kind: MetadataSourceKind, payload: dict[str, Any], url: str | None, retrieved_at: datetime) -> None:
        """Inserts or replaces the raw provider metadata of one dataset and source kind.

        Args:
            dataset_id: The dataset ID.
            kind: The kind of source the payload comes from.
            payload: The metadata as received.
            url: The location the payload was read from.
            retrieved_at: When the payload was read.
        """

    @abstractmethod
    def save_raw_many(self, raw_records: Sequence[RawMetadataRecord]) -> None:
        """Inserts or replaces many raw provider metadata entries; entries too large for the store are skipped and logged.

        Args:
            raw_records: The raw metadata, at most one per dataset and kind.
        """

    @abstractmethod
    def get_raw(self, dataset_id: str, kind: MetadataSourceKind) -> RawMetadataRecord | None:
        """Reads the raw provider metadata of one dataset and source kind.

        Args:
            dataset_id: The dataset ID.
            kind: The kind of source.

        Returns:
            The raw metadata, or None if none is stored.
        """

    @abstractmethod
    def get_raw_many(self, dataset_ids: Sequence[str], kind: MetadataSourceKind) -> dict[str, RawMetadataRecord]:
        """Reads the raw provider metadata of many datasets for one source kind.

        Args:
            dataset_ids: The dataset IDs.
            kind: The kind of source.

        Returns:
            The raw metadata keyed by dataset ID; datasets without raw metadata of this kind are left out.
        """

    @abstractmethod
    def get_raw_dataset_ids(self, kind: MetadataSourceKind) -> set[str]:
        """Lists the datasets that have stored raw metadata of one source kind, without reading the payloads.

        Args:
            kind: The kind of source.

        Returns:
            The dataset IDs.
        """

    @abstractmethod
    def delete(self, dataset_id: str) -> None:
        """Removes the record and all raw metadata of one dataset.

        Args:
            dataset_id: The dataset ID.
        """


class MongoMetadataRepository(MetadataRepository):
    """Metadata repository on MongoDB.

    Normalized records live in 'dataset_metadata' with the dataset ID as `_id`;
    raw provider metadata lives in 'dataset_metadata_raw', one document per dataset and source kind.
    """

    RECORD_COLLECTION = "dataset_metadata"
    RAW_COLLECTION = "dataset_metadata_raw"
    BATCH_SIZE = 1000
    # MongoDB rejects documents above 16 MiB, and one such document fails its whole batch.
    MAX_DOCUMENT_BYTES = 16 * 1024 * 1024

    def __init__(self, db: Database) -> None:
        """Initializes the repository; the indexes are created before the first write.

        Args:
            db: The MongoDB database instance.
        """
        self.records = db[self.RECORD_COLLECTION]
        self.raw_records = db[self.RAW_COLLECTION]
        self._indexes_ensured = False

    def _ensure_indexes(self) -> None:
        """Creates the index for lookups by collection and the unique key of the raw metadata, once per instance."""
        if self._indexes_ensured:
            return
        self.records.create_index("collection")
        self.raw_records.create_index([("dataset_id", ASCENDING), ("kind", ASCENDING)], unique=True)
        self._indexes_ensured = True

    def save(self, record: DatasetMetadata) -> None:
        """Inserts or replaces the record of one dataset.

        Args:
            record: The normalized metadata record.
        """
        self.save_many([record])

    def save_many(self, records: Sequence[DatasetMetadata]) -> None:
        """Inserts or replaces the records of many datasets in batches of BATCH_SIZE.

        Args:
            records: The normalized metadata records.
        """
        self._ensure_indexes()
        operations = [ReplaceOne({"_id": record.dataset_id}, sanitize_for_bson(record.to_document()), upsert=True) for record in records]
        bulk_write_in_batches(self.records, operations, self.BATCH_SIZE)

    def get(self, dataset_id: str) -> DatasetMetadata | None:
        """Reads the record of one dataset.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The record, or None if the dataset has none.
        """
        document = self.records.find_one({"_id": dataset_id})
        return DatasetMetadata.from_document(document) if document else None

    def get_many(self, dataset_ids: Sequence[str]) -> dict[str, DatasetMetadata]:
        """Reads the records of many datasets; a record that does not match the schema is logged and left out.

        One unreadable record would otherwise block every other record read with it.

        Args:
            dataset_ids: The dataset IDs.

        Returns:
            The readable records keyed by dataset ID; datasets without readable record are left out.
        """
        records: dict[str, DatasetMetadata] = {}
        unreadable: list[str] = []
        for document in self.records.find({"_id": {"$in": list(dataset_ids)}}):
            try:
                records[document["_id"]] = DatasetMetadata.from_document(document)
            except ValidationError:
                unreadable.append(str(document["_id"]))
        if unreadable:
            logger.warning("%d stored metadata records do not match the schema and are left out: %s", len(unreadable), unreadable)
        return records

    def find_by_collection(self, collection: str) -> list[DatasetMetadata]:
        """Reads the records of the datasets stored in a MongoDB collection.

        Args:
            collection: The name of the collection holding the data documents.

        Returns:
            The records, sorted by dataset ID; empty if none matches.
        """
        documents = self.records.find({"collection": collection}).sort("_id", ASCENDING)
        return [DatasetMetadata.from_document(document) for document in documents]

    def save_raw(self, dataset_id: str, kind: MetadataSourceKind, payload: dict[str, Any], url: str | None, retrieved_at: datetime) -> None:
        """Inserts or replaces the raw provider metadata of one dataset and source kind.

        Args:
            dataset_id: The dataset ID.
            kind: The kind of source the payload comes from.
            payload: The metadata as received.
            url: The location the payload was read from.
            retrieved_at: When the payload was read.
        """
        self.save_raw_many([RawMetadataRecord(dataset_id=dataset_id, kind=kind, payload=payload, url=url, retrieved_at=retrieved_at)])

    def save_raw_many(self, raw_records: Sequence[RawMetadataRecord]) -> None:
        """Inserts or replaces many raw provider metadata entries in batches of BATCH_SIZE.

        Entries whose document exceeds MAX_DOCUMENT_BYTES are skipped with a warning; a stored older entry stays.

        Args:
            raw_records: The raw metadata, at most one per dataset and kind.
        """
        self._ensure_indexes()
        operations = []
        for raw_record in raw_records:
            key = {"dataset_id": raw_record.dataset_id, "kind": raw_record.kind}
            # The payload is sanitized directly, as model_dump would convert objects without BSON type into iterators
            document = {**key, "url": raw_record.url, "retrieved_at": raw_record.retrieved_at, "payload": sanitize_for_bson(raw_record.payload)}
            document_bytes = len(encode(document))
            if document_bytes > self.MAX_DOCUMENT_BYTES:
                logger.warning(
                    "Raw %s metadata of '%s' has %d bytes, more than MongoDB stores in one document; not saved.",
                    raw_record.kind,
                    raw_record.dataset_id,
                    document_bytes,
                )
                continue
            operations.append(ReplaceOne(key, document, upsert=True))
        bulk_write_in_batches(self.raw_records, operations, self.BATCH_SIZE)

    def get_raw(self, dataset_id: str, kind: MetadataSourceKind) -> RawMetadataRecord | None:
        """Reads the raw provider metadata of one dataset and source kind.

        Args:
            dataset_id: The dataset ID.
            kind: The kind of source.

        Returns:
            The raw metadata, or None if none is stored.
        """
        document = self.raw_records.find_one({"dataset_id": dataset_id, "kind": kind}, {"_id": 0})
        return RawMetadataRecord.model_validate(document) if document else None

    def get_raw_many(self, dataset_ids: Sequence[str], kind: MetadataSourceKind) -> dict[str, RawMetadataRecord]:
        """Reads the raw provider metadata of many datasets for one source kind.

        Args:
            dataset_ids: The dataset IDs.
            kind: The kind of source.

        Returns:
            The raw metadata keyed by dataset ID; datasets without raw metadata of this kind are left out.
        """
        documents = self.raw_records.find({"dataset_id": {"$in": list(dataset_ids)}, "kind": kind}, {"_id": 0})
        return {document["dataset_id"]: RawMetadataRecord.model_validate(document) for document in documents}

    def get_raw_dataset_ids(self, kind: MetadataSourceKind) -> set[str]:
        """Lists the datasets that have stored raw metadata of one source kind, without reading the payloads.

        Args:
            kind: The kind of source.

        Returns:
            The dataset IDs.
        """
        return {str(dataset_id) for dataset_id in self.raw_records.distinct("dataset_id", {"kind": kind})}

    def delete(self, dataset_id: str) -> None:
        """Removes the record and all raw metadata of one dataset.

        Args:
            dataset_id: The dataset ID.
        """
        self.records.delete_one({"_id": dataset_id})
        self.raw_records.delete_many({"dataset_id": dataset_id})
