from datetime import datetime
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    TypeAlias,
    TypedDict,
    runtime_checkable,
)

import numpy as np
import pandas as pd
import xarray as xr
from pymongo.database import Database
from sunpy.timeseries import GenericTimeSeries

# --- TypedDict Definitions for Stricter Structure ---


class TimeRangeDict(TypedDict):
    """Represents a start and end time pair in the catalog."""

    start_time: str  # ISO formatted string
    end_time: str  # ISO formatted string


class CatalogEntryDict(TypedDict):
    """
    A dictionary representing a single source entry in the catalog.

    Example:
        {
            "type": "HAPI",
            "source_id": "http://hapi-server.org/data",
            "time_range": {"start_time": "...", "end_time": "..."}
        }
    """

    type: str
    source_id: str
    time_range: TimeRangeDict


class MasterCatalogItem(TypedDict):
    """Represents a top-level item in the master catalog JSON."""

    dataset_id: str
    sources: List[CatalogEntryDict]


# --- Core Type Aliases ---

DocumentDict: TypeAlias = Dict[str, Any]
"""A dictionary representing a MongoDB document."""

MetadataDict: TypeAlias = Dict[str, Any]
"""A dictionary containing metadata about a dataset or variable."""

TimeRange: TypeAlias = Tuple[datetime, datetime]
"""A tuple representing a time range with (start_time, end_time)."""


# --- ETL-specific Type Aliases ---

RawData: TypeAlias = Optional[pd.DataFrame]
"""
Raw data extracted from a source, typically as a DataFrame or None on failure.
Note: Some extractors might return lists of dicts, so Any is sometimes used in protocols,
but this alias covers the common tabular case.
"""

TransformedDocuments: TypeAlias = List[DocumentDict]
"""A list of documents ready for MongoDB insertion."""

ParsedFileData: TypeAlias = Optional[GenericTimeSeries | List[Tuple[xr.Dataset, str, str]]]
"""Parsed file data, either 1D TimeSeries or list of N-D Dataset tuples (dataset, time_dim, time_coord)."""


# --- Catalog-specific Type Aliases ---

CatalogMap: TypeAlias = Dict[str, List[CatalogEntryDict]]
"""A mapping from dataset_id to list of source entries."""

DiscoveryYield: TypeAlias = Tuple[str, CatalogEntryDict]
"""A tuple of (dataset_id, catalog_entry) yielded by discovery processes."""


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

    def _extract(self) -> Any:
        """
        Extracts raw data from the source.

        Returns:
            Raw extracted data in a processor-specific format, or None on failure.
        """
        ...

    def _transform(self, raw_data: Any) -> TransformedDocuments:
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
    ) -> Tuple[Optional[np.ndarray], Optional[MetadataDict]]:
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
