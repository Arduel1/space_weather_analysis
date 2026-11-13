from datetime import datetime
from enum import StrEnum
from typing import NotRequired, TypedDict


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
    CDAWEB = "CDAWEB"
    GFZ_POTSDAM = "GFZ_POTSDAM"
    LOCAL = "LOCAL"
    SWPC = "SWPC"
    # Image sequences of providers that list their latest frames, kept as scaled files with one document per frame
    IMAGES = "IMAGES"
    # Space weather events with the links between them from DONKI (Database Of Notifications, Knowledge, Information) of
    # the Community Coordinated Modeling Center of NASA
    DONKI = "DONKI"
    # Daily sunspot regions of NOAA (US National Oceanic and Atmospheric Administration) from the Heliophysics Event
    # Knowledgebase (HEK) of the Lockheed Martin Solar and Astrophysics Laboratory (LMSAL)
    HEK = "HEK"
    # Synoptic magnetograms (maps of the solar surface field over one rotation) of GONG, the Global Oscillation Network
    # Group, from which the coronal magnetic field is computed
    GONG = "GONG"
    # Positions of planets and spacecraft from the built-in planetary ephemeris of astropy and from Horizons, the ephemeris
    # service of the Jet Propulsion Laboratory (JPL)
    EPHEMERIS = "EPHEMERIS"
    # Maps of the OVATION aurora model of SWPC (NOAA Space Weather Prediction Center), of which only the newest is published
    OVATION = "OVATION"


class CatalogEntryDict(TypedDict):
    """A dictionary representing a single source entry in the catalog.

    This structure maps a specific data source to a dataset type and the
    time range available at that source.

    Attributes:
        type: The source type, the value of a SourceType member; stored documents hold it as plain text.
        source_id: The identifier of the source within its type: the server URL for HAPI,
            the group name for CDAWEB and 'GFZ' for GFZ_POTSDAM.
        time_range: The temporal coverage of this dataset at this source.
        dataset_id: Optional dataset identifier, set on the GFZ sources.
        description: Optional human-readable description of the dataset.
        ongoing: Whether the source still receives new data, so its stop time moves forward; set by the
            availability refreshes.
        availability_checked_at: When an availability refresh last found the dataset at its source, in UTC.
        missing_runs: How many refreshes in a row did not find the dataset at its source;
            0 once it is found again.
        missing_since: When a refresh first missed the dataset in the current run of misses,
            in UTC; absent while the dataset is found.
        cadence: The time between two records as ISO 8601 duration, set on the sources of the static catalog
            file; it sizes the fetched chunks while the dataset has no metadata record giving it.
    """

    type: str
    source_id: str
    time_range: TimeRangeDict
    dataset_id: NotRequired[str]
    description: NotRequired[str]
    ongoing: NotRequired[bool]
    availability_checked_at: NotRequired[datetime]
    missing_runs: NotRequired[int]
    missing_since: NotRequired[datetime]
    cadence: NotRequired[str]


class MasterCatalogItem(TypedDict):
    """Represents a top-level item in the master catalog JSON structure.

    The master catalog groups multiple sources under a single unique dataset_id.

    Attributes:
        dataset_id: The unique identifier for the dataset (canonical ID).
        sources: A list of available sources providing this dataset.
    """

    dataset_id: str
    sources: list[CatalogEntryDict]


# The sources of each dataset, keyed by dataset ID, for fast catalog lookups.
type CatalogMap = dict[str, list[CatalogEntryDict]]
