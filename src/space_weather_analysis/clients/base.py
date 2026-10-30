from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from space_weather_analysis.catalog.models import SourceType


@dataclass(frozen=True)
class FetchResult[T]:
    """The answer of a data provider for one dataset and time range.

    Attributes:
        data: The data in the format of the client; None if the provider has no data for the range.
        source_url: The server that answered; None if the client asked no server.
    """

    data: T | None
    source_url: str | None = None


class DataClient[T](ABC):
    """The contract by which the pipelines fetch data from a provider.

    Every client fetches the data of one dataset for one time range that starts inclusive and
    stops exclusive, both given as timezone-aware times. A failed request raises, while a provider
    answering that it has no data for the range gives a result without data, so callers can tell
    a gap that is worth fetching again from a range without data.

    Attributes:
        source_type: The source type of the master catalog the client serves.
    """

    source_type: ClassVar[SourceType]

    @abstractmethod
    def fetch(self, dataset_id: str, start: datetime, stop: datetime) -> FetchResult[T]:
        """Fetches the data of a dataset for a time range.

        Args:
            dataset_id: The dataset ID in the master catalog.
            start: The start of the range (inclusive), timezone-aware.
            stop: The end of the range (exclusive), timezone-aware.

        Returns:
            The data of the range and the server that delivered it.

        Raises:
            DataRetrievalError: If the request failed; carries the wait the provider asked for, if any.
            DataProcessingError: If the response cannot be read.
        """
