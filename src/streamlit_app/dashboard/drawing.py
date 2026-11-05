from collections.abc import Mapping
from datetime import datetime, timedelta

from space_weather_analysis.dashboard.freshness import Freshness, age_text, expected_age, freshness, worst
from space_weather_analysis.dashboard.labels import Metadata

# Height of chart and table panels in pixels, so that the whole grid fits on one screen
PANEL_HEIGHT = 300
# Line break inside Streamlit help texts and captions, which render markdown
HELP_LINE_BREAK = "  \n"
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
