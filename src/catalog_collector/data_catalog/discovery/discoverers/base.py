from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any, ClassVar

from space_weather_analysis.catalog.models import CatalogEntryDict, SourceType

# The output unit of discovery: dataset ID, catalog entry, and the provider metadata of the dataset or None.
type DiscoveryYield = tuple[str, CatalogEntryDict, dict[str, Any] | None]


class AbstractDiscoverer(ABC):
    """A discovery strategy that lists the datasets of one source type with their catalog entries.

    A source that could not be read is named in failed_source_ids, so the builder does not count its datasets
    as missing; a failure that leaves nothing discovered raises instead.

    Attributes:
        source_type: The source type of the entries the discoverer yields.
        failed_source_ids: The source IDs whose datasets could not be listed in the last run.
    """

    source_type: ClassVar[SourceType]

    def __init__(self) -> None:
        """Initializes the discoverer without failed sources."""
        self.failed_source_ids: set[str] = set()

    @abstractmethod
    def discover(self) -> Iterator[DiscoveryYield]:
        """Lists the datasets of the source type one by one, so the whole result is never held in memory.

        Yields:
            The dataset ID, catalog entry and provider metadata of each discovered dataset.

        Raises:
            DataRetrievalError: If nothing could be discovered at all.
        """
