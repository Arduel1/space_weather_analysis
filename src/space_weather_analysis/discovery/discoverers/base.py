from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, Tuple


class AbstractDiscoverer(ABC):
    """
    Abstract Base Class for a catalog discovery strategy.

    Each concrete implementation of this class is responsible for querying
    a specific type of data source and yielding standardized catalog entries.
    """

    @abstractmethod
    def discover(self) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
        """
        Runs the discovery process for the specific source.

        This method should yield its findings one by one as a generator
        to minimize memory usage.

        Yields:
            A generator of (dataset_id, catalog_entry) tuples.
        """
        pass
