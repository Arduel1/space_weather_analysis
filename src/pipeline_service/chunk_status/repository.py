from abc import ABC, abstractmethod
from datetime import datetime

from pymongo import ASCENDING
from pymongo.database import Database

from pipeline_service.chunk_status.models import ChunkStatus


class ChunkStatusRepository(ABC):
    """Stores the status of the last successful fetch of each chunk, so that chunked runs skip chunks that need no new fetch."""

    @abstractmethod
    def get(self, dataset_id: str, cell_start: datetime) -> ChunkStatus | None:
        """Reads the status of one chunk.

        Args:
            dataset_id: The dataset ID.
            cell_start: The start of the grid cell of the chunk.

        Returns:
            The status, or None if the chunk was never fetched successfully.
        """

    @abstractmethod
    def save(self, status: ChunkStatus) -> None:
        """Inserts or replaces the status of one chunk.

        Args:
            status: The status to store.
        """


class MongoChunkStatusRepository(ChunkStatusRepository):
    """Chunk status repository on MongoDB; each status is one document in 'chunk_status', unique per dataset and cell start."""

    COLLECTION = "chunk_status"

    def __init__(self, db: Database) -> None:
        """Initializes the repository; the index is created before the first write.

        Args:
            db: The MongoDB database instance.
        """
        self.statuses = db[self.COLLECTION]
        self._indexes_ensured = False

    def _ensure_indexes(self) -> None:
        """Creates the unique index on dataset and cell start, once per instance."""
        if self._indexes_ensured:
            return
        self.statuses.create_index([("dataset_id", ASCENDING), ("cell_start", ASCENDING)], unique=True)
        self._indexes_ensured = True

    def get(self, dataset_id: str, cell_start: datetime) -> ChunkStatus | None:
        """Reads the status of one chunk.

        Args:
            dataset_id: The dataset ID.
            cell_start: The start of the grid cell of the chunk.

        Returns:
            The status, or None if the chunk was never fetched successfully.
        """
        document = self.statuses.find_one({"dataset_id": dataset_id, "cell_start": cell_start}, {"_id": 0})
        return ChunkStatus.model_validate(document) if document else None

    def save(self, status: ChunkStatus) -> None:
        """Inserts or replaces the status of one chunk.

        Args:
            status: The status to store.
        """
        self._ensure_indexes()
        self.statuses.replace_one({"dataset_id": status.dataset_id, "cell_start": status.cell_start}, status.model_dump(), upsert=True)
