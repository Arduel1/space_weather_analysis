from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    TypeAlias,
)

from space_weather_analysis.catalog.models import CatalogEntryDict

if TYPE_CHECKING:
    # Both packages take seconds to import; the alias that needs them is evaluated only by type checkers.
    import xarray as xr
    from sunpy.timeseries import GenericTimeSeries

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


# --- Discovery-specific Type Aliases ---

DiscoveryYield: TypeAlias = Tuple[str, CatalogEntryDict, Optional[Dict[str, Any]]]
"""
The output unit of discovery processes.
Structure: (dataset_id, catalog_entry_dictionary, dataset_metadata or None).
"""
