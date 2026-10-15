from datetime import datetime
from typing import (
    Any,
    Dict,
    List,
    NotRequired,
    Optional,
    Protocol,
    Tuple,
    TypeAlias,
    TypedDict,
    runtime_checkable,
)

import pandas as pd
import xarray as xr
from pymongo.database import Database
from sunpy.timeseries import GenericTimeSeries

# --- TypedDict Definitions for Stricter Structure ---


class TimeRangeDict(TypedDict):
    """
    Represents a start and end time pair in the catalog.

    Attributes:
        start_time: The start timestamp in ISO 8601 format (e.g., '2023-01-01T00:00:00Z').
        end_time: The end timestamp in ISO 8601 format.
    """

    start_time: str
    end_time: str


class CatalogEntryDict(TypedDict):
    """
    A dictionary representing a single source entry in the catalog.

    This structure maps a specific data source (like a HAPI server URL)
    to a dataset type and the time range available at that source.

    Attributes:
        type: The type identifier of the source (e.g., 'HAPI', 'FIDO_CDAWEB').
        source_id: The specific identifier for the source (e.g., URL for HAPI, Group Name for CDAWeb).
        time_range: The temporal coverage of this dataset at this source.
        dataset_id: Optional dataset identifier, added by some discoverers (e.g. CDAWeb, GFZ).
        description: Optional human-readable description of the dataset (e.g. GFZ).

    Example:
        {
            "type": "HAPI",
            "source_id": "http://hapi-server.org/data",
            "time_range": {"start_time": "2020-01-01T00:00:00Z", "end_time": "2020-01-02T00:00:00Z"}
        }
    """

    type: str
    source_id: str
    time_range: TimeRangeDict
    dataset_id: NotRequired[str]
    description: NotRequired[str]


class MasterCatalogItem(TypedDict):
    """
    Represents a top-level item in the master catalog JSON structure.

    The master catalog groups multiple sources under a single unique dataset_id.

    Attributes:
        dataset_id: The unique identifier for the dataset (canonical ID).
        sources: A list of available sources providing this dataset.
    """

    dataset_id: str
    sources: List[CatalogEntryDict]


# --- Core Type Aliases ---

DocumentDict: TypeAlias = Dict[str, Any]
"""
A dictionary representing a MongoDB document.
Keys are strings (field names), values can be any BSON-compatible type.
"""

MetadataDict: TypeAlias = Dict[str, Any]
"""
A dictionary containing metadata about a dataset or variable.
Typically contains keys like 'units', 'description', etc.
"""

TimeRange: TypeAlias = Tuple[datetime, datetime]
"""
A tuple representing a time range: (start_time, end_time).
Both datetimes should be timezone-aware (UTC).
"""


# --- ETL-specific Type Aliases ---

RawData: TypeAlias = Optional[pd.DataFrame | List[Dict[str, Any]]]
"""
Raw data extracted from a source.
- Typically a `pd.DataFrame` for tabular data (Sunspots, CME).
- Can be a `List[Dict]` for document-oriented sources (Catalog).
- Returns `None` if extraction fails.
"""

TransformedDocuments: TypeAlias = List[DocumentDict]
"""A list of formatted dictionaries ready for MongoDB insertion."""

ParsedFileData: TypeAlias = Optional[GenericTimeSeries | List[Tuple[xr.Dataset, str, str]]]
"""
Parsed file data structure.
Returns:
    - `GenericTimeSeries`: For 1D data (CSV, simple CDF).
    - `List[Tuple[xr.Dataset, str, str]]`: For N-D data (NetCDF, complex CDF).
      Tuple structure: (dataset_object, time_dimension_name, time_coordinate_name).
    - `None`: If parsing fails.
"""


# --- Catalog-specific Type Aliases ---

CatalogMap: TypeAlias = Dict[str, List[CatalogEntryDict]]
"""
A mapping for fast catalog lookups.
Key: dataset_id
Value: List of available sources (CatalogEntryDict).
"""

DiscoveryYield: TypeAlias = Tuple[str, CatalogEntryDict]
"""
The output unit of discovery processes.
Structure: (dataset_id, catalog_entry_dictionary).
"""

# --- Protocols ---


@runtime_checkable
class ETLProcessor(Protocol):
    """
    Protocol defining the interface for ETL processors.

    All ETL processors must implement the extract, transform, and run methods
    with these exact signatures to ensure interoperability.
    """

    def __init__(self, db: Database, **kwargs: Any) -> None:
        """
        Initializes the ETL processor.

        Args:
            db: The MongoDB database instance.
            **kwargs: Additional processor-specific configuration.
        """
        ...

    def _extract(self) -> RawData:
        """
        Extracts raw data from the source.

        Returns:
            RawData: The extracted data (DataFrame or List of Dicts), or None on failure.
        """
        ...

    def _transform(self, raw_data: RawData) -> TransformedDocuments:
        """
        Transforms raw data into MongoDB documents.

        Args:
            raw_data: The raw data from the extract step.

        Returns:
            A list of transformed documents ready for database insertion.
        """
        ...

    def run(self) -> None:
        """
        Executes the complete ETL process.

        This method orchestrates the extract, transform, and load operations.
        """
        ...


@runtime_checkable
class DataClient(Protocol):
    """
    Protocol defining the interface for data clients.

    All data clients must implement these methods to enable consistent
    data retrieval across different source types.
    """

    def get_metadata(self, dataset_id: str) -> Optional[MetadataDict]:
        """
        Retrieves metadata for a specific dataset.

        Args:
            dataset_id: The unique identifier of the dataset.

        Returns:
            A dictionary containing dataset metadata, or None if unavailable.
        """
        ...

    def get_data_and_meta(
        self, dataset_id: str, start_time: datetime, end_time: datetime, **kwargs: Any
    ) -> Tuple[Optional[Any], Optional[MetadataDict]]:
        """
        Retrieves both data and metadata for a dataset within a time range.

        Args:
            dataset_id: The unique identifier of the dataset.
            start_time: The start of the requested time range.
            end_time: The end of the requested time range.
            **kwargs: Additional client-specific parameters.

        Returns:
            A tuple containing the data array and metadata dictionary,
            or (None, None) if retrieval fails.
        """
        ...
