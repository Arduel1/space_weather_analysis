from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    NotRequired,
    Optional,
    Tuple,
    TypeAlias,
    TypedDict,
)

if TYPE_CHECKING:
    # Both packages take seconds to import; the alias that needs them is evaluated only by type checkers.
    import xarray as xr
    from sunpy.timeseries import GenericTimeSeries

# --- TypedDict Definitions for Stricter Structure ---


class TimeRangeDict(TypedDict):
    """Represents a start and end time pair in the catalog.

    Both values are date or date-time strings in the format of the source that
    reported them; they are not normalized to a common format.

    Attributes:
        start_time: The start of the time range.
        end_time: The end of the time range.
    """

    start_time: str
    end_time: str


class CatalogEntryDict(TypedDict):
    """A dictionary representing a single source entry in the catalog.

    This structure maps a specific data source to a dataset type and the
    time range available at that source.

    Attributes:
        type: The source type identifier: 'HAPI', 'FIDO_CDAWEB' or 'GFZ_POTSDAM'.
        source_id: The identifier of the source within its type: the server URL for HAPI,
            the group name for FIDO_CDAWEB and 'GFZ' for GFZ_POTSDAM.
        time_range: The temporal coverage of this dataset at this source.
        dataset_id: Optional dataset identifier, added by some discoverers.
        description: Optional human-readable description of the dataset.
    """

    type: str
    source_id: str
    time_range: TimeRangeDict
    dataset_id: NotRequired[str]
    description: NotRequired[str]


class MasterCatalogItem(TypedDict):
    """Represents a top-level item in the master catalog JSON structure.

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

TimeRange: TypeAlias = Tuple[datetime, datetime]
"""
A tuple representing a time range: (start_time, end_time).
Both datetimes should be timezone-aware (UTC).
"""


# --- ETL-specific Type Aliases ---

TransformedDocuments: TypeAlias = List[DocumentDict]
"""A list of formatted dictionaries ready for MongoDB insertion."""

type ParsedFileData = Optional[GenericTimeSeries | List[Tuple[xr.Dataset, str, str]]]
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

DiscoveryYield: TypeAlias = Tuple[str, CatalogEntryDict, Optional[Dict[str, Any]]]
"""
The output unit of discovery processes.
Structure: (dataset_id, catalog_entry_dictionary, dataset_metadata or None).
"""
