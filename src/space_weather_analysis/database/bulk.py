import logging
from typing import Sequence, Union

from pymongo import ReplaceOne, UpdateOne
from pymongo.collection import Collection

logger = logging.getLogger(__name__)


def bulk_write_in_batches(collection: Collection, operations: Sequence[Union[ReplaceOne, UpdateOne]], batch_size: int) -> None:
    """Executes replace or update operations in batches, unordered within each batch.

    Args:
        collection: The target collection.
        operations: The replace or update operations to execute.
        batch_size: The maximum number of operations sent in one bulk write.

    Raises:
        pymongo.errors.BulkWriteError: If a batch fails to write.
    """
    for batch_start in range(0, len(operations), batch_size):
        batch = operations[batch_start : batch_start + batch_size]
        result = collection.bulk_write(batch, ordered=False)
        logger.debug(f"Bulk write to '{collection.name}': matched {result.matched_count}, upserted {result.upserted_count}")
