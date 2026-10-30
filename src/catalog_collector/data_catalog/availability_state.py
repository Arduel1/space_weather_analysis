from datetime import datetime, timedelta, timezone

from space_weather_analysis.catalog.models import CatalogEntryDict

# Datasets updated monthly or with some lag still count as ongoing, while ended missions stop years back.
ONGOING_TOLERANCE = timedelta(days=60)


def apply_availability(source: CatalogEntryDict, start: str, stop: str, ongoing: bool, checked_at: str) -> None:
    """Sets the time range of a source to the values its provider reports and clears its missing state.

    Args:
        source: The catalog source, changed in place.
        start: The start time text as given by the provider.
        stop: The stop time text as given by the provider.
        ongoing: Whether the source still receives new data.
        checked_at: The time of this refresh as ISO 8601 UTC text.
    """
    source["time_range"] = {"start_time": start, "end_time": stop}
    source["ongoing"] = ongoing
    source["availability_checked_at"] = checked_at
    source["missing_runs"] = 0
    source.pop("missing_since", None)


def mark_missing(source: CatalogEntryDict, checked_at: str) -> None:
    """Counts one more refresh that missed the dataset of a source and keeps everything else.

    Args:
        source: The catalog source, changed in place.
        checked_at: The time of this refresh as ISO 8601 UTC text.
    """
    source["missing_runs"] = source.get("missing_runs", 0) + 1
    if "missing_since" not in source:
        source["missing_since"] = checked_at


def format_utc(time: datetime) -> str:
    """Formats a UTC time as ISO 8601 text with seconds and `Z`.

    Args:
        time: The aware time.

    Returns:
        The time text.
    """
    return time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
