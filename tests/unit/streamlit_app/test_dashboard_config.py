import pathlib
from datetime import datetime

import pytest
from pydantic import ValidationError

from streamlit_app.dashboard.config import load_dashboard_config, panel_from_table
from streamlit_app.dashboard.panel_types import PANEL_TYPES, PanelType
from streamlit_app.dashboard.panels.base import PanelConfig
from streamlit_app.dashboard.panels.timeseries import TimeseriesPanel
from streamlit_app.dashboard.panels.value import ValuePanel

PROJECT_FEED_FILE = pathlib.Path(__file__).parents[3] / "config" / "feeds.toml"
FEED = '[[feed]]\nname = "live"\ndatasets = ["a_set", "b_set"]\n\n'
VALUE_PANEL = '[[dashboard.panel]]\ntype = "value"\nrow = 1\ndataset = "a_set"\nfield = "flux"\n'
TIMESERIES_PANEL = (
    '[[dashboard.panel]]\ntype = "timeseries"\nrow = 1\nview = "PT24H"\nhistory = "P7D"\nseries = [{ dataset = "a_set", field = "flux" }]\n'
)


def _write(directory: pathlib.Path, content: str) -> pathlib.Path:
    """Writes a feed file into a directory and returns its path."""
    path = directory / "feeds.toml"
    path.write_text(content, encoding="utf-8")
    return path


class GaugePanel(PanelConfig):
    """A panel type known only to the tests, with one setting of its own.

    Attributes:
        needle: The position of the needle.
    """

    needle: float


def _draw_nothing(panel: GaugePanel, now: datetime, key: str) -> None:
    """Draws nothing, as the tests only validate gauge panels."""
    del panel, now, key


def test_project_dashboard_table_is_valid() -> None:
    """The dashboard table of the project feed file loads with known panel types and shows the live X-ray collection."""
    config = load_dashboard_config(PROJECT_FEED_FILE)

    assert {panel.type for panel in config.panel} <= PANEL_TYPES.keys()
    assert "swpc_goes_xray" in config.collections()


def test_panels_are_validated_by_their_type_and_grouped_into_rows(tmp_path: pathlib.Path) -> None:
    """Each panel comes back as the model of its type, and rows are sorted by number with the panels in file order."""
    content = (
        FEED
        + VALUE_PANEL.replace("row = 1", "row = 2")
        + 'title = "first"\n'
        + TIMESERIES_PANEL
        + 'title = "top"\n'
        + VALUE_PANEL.replace("row = 1", "row = 2")
        + 'title = "second"\n'
    )

    config = load_dashboard_config(_write(tmp_path, content))

    assert [[(type(panel), panel.title) for panel in row] for row in config.rows()] == [
        [(TimeseriesPanel, "top")],
        [(ValuePanel, "first"), (ValuePanel, "second")],
    ]


@pytest.mark.parametrize(
    "dashboard",
    [
        pytest.param('[[dashboard.panel]]\ntype = "gauge"\nrow = 1\n', id="unknown-panel-type"),
        pytest.param(VALUE_PANEL.replace("a_set", "unfed"), id="dataset-of-no-feed"),
        pytest.param(VALUE_PANEL + 'feild = "flux"\n', id="unknown-key"),
        pytest.param(VALUE_PANEL + 'scale = "noaa_x"\n', id="unknown-scale"),
        pytest.param(VALUE_PANEL + 'scale = "noaa_g"\nthresholds = [{ value = 1.0, label = "high" }]\n', id="scale-and-thresholds"),
        pytest.param(TIMESERIES_PANEL.replace("P7D", "PT1H"), id="history-shorter-than-view"),
        pytest.param(TIMESERIES_PANEL + 'y_axis = "log"\ny_range = [0, 10]\n', id="log-axis-with-non-positive-range"),
        pytest.param(VALUE_PANEL + 'format = "{value}"\n', id="no-format-string"),
    ],
)
def test_invalid_dashboard_table_is_rejected(tmp_path: pathlib.Path, dashboard: str) -> None:
    """Unknown types, keys and scales, datasets of no feed and contradicting or unusable settings fail validation."""
    with pytest.raises(ValidationError):
        load_dashboard_config(_write(tmp_path, FEED + dashboard))


def test_a_registered_panel_type_validates_its_own_settings() -> None:
    """A new panel type needs only its model and a registry entry to validate tables of its type."""
    panel_types = {"gauge": PanelType(GaugePanel, _draw_nothing)}

    panel = panel_from_table({"type": "gauge", "row": 1, "needle": 0.5}, panel_types)

    assert panel == GaugePanel(type="gauge", row=1, needle=0.5)
