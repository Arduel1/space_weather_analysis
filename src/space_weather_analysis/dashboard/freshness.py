from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from space_weather_analysis.dashboard.labels import Metadata
from space_weather_analysis.metadata.models import TimestampLocation
from space_weather_analysis.util.time_utils import SecondsDuration, parse_iso_duration

# Delay of a provider between a measurement and its publication, allowed on top of the expected age
PUBLICATION_DELAY = timedelta(minutes=15)
# Multiple of the expected age up to which data count as delayed instead of stale
DELAYED_FACTOR = 4


class Freshness(StrEnum):
    """How current the newest stored value of a dataset is."""

    LIVE = "live"
    DELAYED = "delayed"
    STALE = "stale"
    # Data of a feed without interval, which is loaded only on start
    STATIC = "static"


# Order from best to worst, so that a panel with several datasets shows the worst state
_SEVERITY = {Freshness.STATIC: 0, Freshness.LIVE: 1, Freshness.DELAYED: 2, Freshness.STALE: 3}


def expected_age(interval: timedelta | None, metadata: Metadata) -> timedelta | None:
    """Returns how old the newest value may be in normal operation.

    Args:
        interval: The interval of the feed loading the dataset; None for a feed loaded only on start.
        metadata: The metadata record of the dataset, with cadence and time stamp location if known.

    Returns:
        The expected age plus PUBLICATION_DELAY; None for a feed without interval.
    """
    if interval is None:
        return None
    # A feed fetches every interval, a value appears at most one cadence after the previous one, and a time stamp at the
    # start of its measurement interval lies one more cadence back when the value is published
    time_info = metadata.get("time", {})
    cadence = parse_iso_duration(time_info.get("cadence") or "") or timedelta(0)
    begin_stamp = cadence if time_info.get("timestamp_location") == TimestampLocation.BEGIN else timedelta(0)
    return interval + cadence + begin_stamp + PUBLICATION_DELAY


def freshness(newest: datetime, now: datetime, expected: timedelta | None) -> Freshness:
    """Rates the age of the newest value against the expected age.

    Args:
        newest: The time stamp of the newest stored value.
        now: The current time.
        expected: The expected age from expected_age; None for a feed without interval.

    Returns:
        STATIC without expected age, LIVE up to the expected age, DELAYED up to DELAYED_FACTOR times it, else STALE.
    """
    if expected is None:
        return Freshness.STATIC
    age = now - newest
    if age <= expected:
        return Freshness.LIVE
    return Freshness.DELAYED if age <= expected * DELAYED_FACTOR else Freshness.STALE


def worst(states: list[Freshness]) -> Freshness | None:
    """Returns the worst of several states; None for no state."""
    return max(states, key=_SEVERITY.__getitem__, default=None)


class DatasetFreshness(BaseModel):
    """How current the newest stored value of one dataset is, for a reader that rates it against its own clock.

    Attributes:
        dataset: The dataset ID.
        newest: The time stamp of the newest stored value.
        expected_age: The expected age from expected_age; None for a feed loaded only on start.
    """

    model_config = ConfigDict(frozen=True)

    dataset: str
    newest: datetime
    expected_age: SecondsDuration | None


def dataset_freshness(dataset: str, newest: datetime, metadata: Metadata, interval: timedelta | None) -> DatasetFreshness:
    """Describes how current a dataset is by its newest value and the age expected from its feed and metadata.

    Args:
        dataset: The dataset ID.
        newest: The time stamp of the newest stored value.
        metadata: The metadata record of the dataset, with cadence and time stamp location if known.
        interval: The interval of the feed loading the dataset; None for a feed loaded only on start.

    Returns:
        The description.
    """
    return DatasetFreshness(dataset=dataset, newest=newest, expected_age=expected_age(interval, metadata))
