import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, Tuple

logger = logging.getLogger(__name__)


class AbstractDiscoverer(ABC):
    """
    Abstract Base Class for a catalog discovery strategy.

    Each concrete implementation of this class is responsible for querying
    a specific type of data source (e.g., HAPI servers, Fido/CDAWeb) and
    yielding standardized catalog entries.
    """

    @abstractmethod
    def discover(self) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
        """
        Runs the discovery process for the specific source.

        This method should yield its findings one by one to keep
        memory usage low.

        Yields:
            Generator[Tuple[str, Dict[str, Any]], None, None]:
                A generator yielding Tuples, where the first element is the
                dataset_id (str) and the second is the catalog entry
                dictionary (Dict[str, Any]).
        """
        pass
