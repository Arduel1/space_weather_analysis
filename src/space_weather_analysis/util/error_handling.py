class SpaceWeatherError(Exception):
    """Base class of the errors this project raises, so callers can catch all of them with one except clause."""


class DataRetrievalError(SpaceWeatherError):
    """Raised when a provider request fails, as opposed to a provider answering that it has no data.

    Attributes:
        retry_after_seconds: The wait the provider asked for before the next request; None if it asked for none.
    """

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        """Initializes the error.

        Args:
            message: What failed.
            retry_after_seconds: The wait the provider asked for before the next request.
        """
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class SourceUnreachableError(DataRetrievalError):
    """Raised when a provider sent no answer at all: the connection failed or timed out, so further requests are pointless for now."""


class ResourceNotFoundError(DataRetrievalError):
    """Raised when a provider answers that the requested document does not exist (HTTP 404)."""


class DataProcessingError(SpaceWeatherError):
    """Raised when received or local data cannot be read, converted or stored."""


class CatalogError(SpaceWeatherError):
    """Raised when catalog operations fail."""


class DatasetNotCataloguedError(CatalogError):
    """Raised when a requested dataset has no source in the master catalog, which may still be adding it."""
