from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Mapping, Sequence


class BaseClient(ABC):
    """Abstract base class for data provider clients.

    This interface unifies the interaction with different space weather data
    sources. It supports both the discovery phase and the retrieval phase.
    """

    @abstractmethod
    def get_catalog_entries(self) -> Sequence[Mapping[str, Any]]:
        """Retrieves a list of available datasets or groups from the provider.

        This is primarily used by the Catalog Service for discovery.

        Returns:
            Sequence[Mapping[str, Any]]: A list of raw dataset entries or groups.
        """
        pass

    @abstractmethod
    def get_dataset_metadata(self, dataset_id: str) -> Dict[str, Any]:
        """Retrieves detailed metadata for a specific dataset.

        Args:
            dataset_id (str): The unique identifier of the dataset.

        Returns:
            Dict[str, Any]: Detailed metadata; the keys depend on the implementation.
        """
        pass

    @abstractmethod
    def get_data(self, dataset_id: str, start_time: datetime, end_time: datetime, **kwargs: Any) -> Any:
        """Fetches time-series data for the specified dataset and time range.

        Args:
            dataset_id (str): The unique identifier of the dataset.
            start_time (datetime): The start of the requested time range.
            end_time (datetime): The end of the requested time range.
            **kwargs: Additional provider-specific arguments.

        Returns:
            Any: The fetched data (structure depends on implementation).
        """
        pass
