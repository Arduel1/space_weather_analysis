from datetime import UTC, datetime, timedelta

from space_weather_analysis.dashboard.panel_types import PANEL_TYPES
from streamlit_app.dashboard.drawing import panel_freshness
from streamlit_app.dashboard.panel_types import PANEL_RENDERERS

NOW = datetime(2024, 1, 1, 12, tzinfo=UTC)


def test_panel_freshness_shows_the_worst_state_with_its_age() -> None:
    """The panel shows the worst state of its datasets with the age of the newest value of that dataset."""
    newest_by_dataset = {
        "live_set": NOW - timedelta(minutes=5),
        "stale_set": NOW - timedelta(hours=3),
        "static_set": NOW - timedelta(days=30),
    }
    intervals = {"live_set": timedelta(minutes=1), "stale_set": timedelta(minutes=1), "static_set": None}

    assert panel_freshness(newest_by_dataset, {}, intervals, NOW) == ":red[stale], newest value 3 h ago"


def test_panel_freshness_without_data_is_empty() -> None:
    """A panel without any stored value gets no freshness text."""
    assert panel_freshness({}, {}, {"live_set": timedelta(minutes=1)}, NOW) == ""


def test_every_panel_type_has_a_renderer() -> None:
    """Streamlit can draw every panel type of the core registry."""
    assert PANEL_RENDERERS.keys() == PANEL_TYPES.keys()
