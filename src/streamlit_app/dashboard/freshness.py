from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum

from space_weather_analysis.metadata.models import TimestampLocation
from space_weather_analysis.util.time_utils import parse_iso_duration
from streamlit_app.dashboard.labels import Metadata

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

    A feed fetches every interval, a value appears at most one cadence after the previous one, and a time stamp at
    the start of its measurement interval lies one more cadence back when the value is published.

    Args:
        interval: The interval of the feed loading the dataset; None for a feed loaded only on start.
        metadata: The metadata record of the dataset, with cadence and time stamp location if known.

    Returns:
        The expected age plus PUBLICATION_DELAY; None for a feed without interval.
    """
    if interval is None:
        return None
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


def age_text(age: timedelta) -> str:
    """Writes an age in its largest whole unit: seconds below a minute, minutes below an hour, hours below two days, else days."""
    seconds = max(int(age.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 2 * 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


# Streamlit markdown color of each state
_COLORS = {Freshness.LIVE: "green", Freshness.DELAYED: "orange", Freshness.STALE: "red", Freshness.STATIC: "gray"}


def freshness_markdown(state: Freshness, newest: datetime, now: datetime) -> str:
    """Writes a state with the age of the newest value as colored Streamlit markdown.

    Args:
        state: The state.
        newest: The time stamp of the newest stored value.
        now: The current time.

    Returns:
        The markdown text.
    """
    return f":{_COLORS[state]}[{state}], newest value {age_text(now - newest)} ago"


def panel_freshness(
    newest_by_dataset: Mapping[str, datetime], metadata_by_dataset: Mapping[str, Metadata], intervals: Mapping[str, timedelta | None], now: datetime
) -> str:
    """Rates the datasets of a panel and writes the worst state with the age of its newest value.

    Args:
        newest_by_dataset: The time stamp of the newest stored value of each dataset with data, by dataset ID.
        metadata_by_dataset: The metadata record of each dataset, by dataset ID; missing or empty if it has none.
        intervals: The feed interval of each dataset, by dataset ID; missing or None for a feed loaded only on start.
        now: The current time.

    Returns:
        The colored markdown of the worst state; empty without any data.
    """
    rated = {
        dataset: freshness(newest, now, expected_age(intervals.get(dataset), metadata_by_dataset.get(dataset, {})))
        for dataset, newest in newest_by_dataset.items()
    }
    worst_state = worst(list(rated.values()))
    if worst_state is None:
        return ""
    worst_dataset = next(dataset for dataset, state in rated.items() if state == worst_state)
    return freshness_markdown(worst_state, newest_by_dataset[worst_dataset], now)
