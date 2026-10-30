from enum import StrEnum
from typing import Dict, List, NotRequired, TypeAlias, TypedDict


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


class SourceType(StrEnum):
    """The client families that ingest the sources of the master catalog.

    The members are strings, so they compare equal to the stored source types and are stored as their value.
    """

    HAPI = "HAPI"
    FIDO_CDAWEB = "FIDO_CDAWEB"
    GFZ_POTSDAM = "GFZ_POTSDAM"
    LOCAL = "LOCAL"


class CatalogEntryDict(TypedDict):
    """A dictionary representing a single source entry in the catalog.

    This structure maps a specific data source to a dataset type and the
    time range available at that source.

    Attributes:
        type: The source type, the value of a SourceType member; stored documents hold it as plain text.
        source_id: The identifier of the source within its type: the server URL for HAPI,
            the group name for FIDO_CDAWEB and 'GFZ' for GFZ_POTSDAM.
        time_range: The temporal coverage of this dataset at this source.
        dataset_id: Optional dataset identifier, added by some discoverers.
        description: Optional human-readable description of the dataset.
        ongoing: Whether the source still receives new data, so its stop time moves forward; set by the
            availability refresh from the HAPI metadata mirror.
        availability_checked_at: When the availability refresh last found the dataset at its source,
            as ISO 8601 UTC time.
        missing_runs: How many availability refreshes in a row did not find the dataset at its server;
            0 once it is found again.
        missing_since: When the availability refresh first missed the dataset in the current run of
            misses, as ISO 8601 UTC time; absent while the dataset is found.
    """

    type: str
    source_id: str
    time_range: TimeRangeDict
    dataset_id: NotRequired[str]
    description: NotRequired[str]
    ongoing: NotRequired[bool]
    availability_checked_at: NotRequired[str]
    missing_runs: NotRequired[int]
    missing_since: NotRequired[str]


class MasterCatalogItem(TypedDict):
    """Represents a top-level item in the master catalog JSON structure.

    The master catalog groups multiple sources under a single unique dataset_id.

    Attributes:
        dataset_id: The unique identifier for the dataset (canonical ID).
        sources: A list of available sources providing this dataset.
    """

    dataset_id: str
    sources: List[CatalogEntryDict]


CatalogMap: TypeAlias = Dict[str, List[CatalogEntryDict]]
"""
A mapping for fast catalog lookups.
Key: dataset_id
Value: List of available sources (CatalogEntryDict).
"""
