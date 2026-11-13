import json
import pathlib
from datetime import timedelta

import pytest
from pydantic import ValidationError

from space_weather_analysis.dashboard.config import load_dashboard_config, load_feed_intervals, panel_from_table
from space_weather_analysis.dashboard.panel_types import PANEL_TYPES, PanelType
from space_weather_analysis.dashboard.panels.base import PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.panels.timeseries import StackPanel
from space_weather_analysis.dashboard.panels.value import ValuePanel
from space_weather_analysis.dashboard.reading import DashboardReader

PROJECT_CONFIG = pathlib.Path(__file__).parents[3] / "config"
FEED = '[[feed]]\nname = "live"\ndatasets = ["a_set", "b_set"]\n'
VALUE_PANEL = '[[panel]]\ntype = "value"\nrow = 1\ndataset = "a_set"\nfield = "flux"\n'
STACK_PANEL = '[[panel]]\ntype = "stack"\nrow = 1\nview = "PT24H"\nhistory = "P7D"\nrows = [{ series = [{ dataset = "a_set", field = "flux" }] }]\n'


def _write(directory: pathlib.Path, pages: dict[str, str], feeds: str = FEED) -> tuple[pathlib.Path, pathlib.Path]:
    """Writes a feed file and a dashboard directory with one page file per entry in the given order.

    Args:
        directory: The directory to write into.
        pages: The content of each page file by page file name.
        feeds: The content of the feed file.

    Returns:
        The dashboard directory and the feed file.
    """
    dashboard_dir = directory / "dashboard"
    dashboard_dir.mkdir()
    (dashboard_dir / "pages.toml").write_text(f"pages = {json.dumps(list(pages))}\n", encoding="utf-8")
    for name, content in pages.items():
        (dashboard_dir / f"{name}.toml").write_text(content, encoding="utf-8")
    feeds_file = directory / "feeds.toml"
    feeds_file.write_text(feeds, encoding="utf-8")
    return dashboard_dir, feeds_file


class GaugePanel(PanelConfig):
    """A panel type known only to the tests, with one setting of its own.

    Attributes:
        needle: The position of the needle.
    """

    needle: float


def _build_nothing(panel: GaugePanel, reader: DashboardReader, context: PanelContext) -> PanelPayload:
    """Builds an empty payload, as the tests only validate gauge panels."""
    del reader, context
    return PanelPayload(type=panel.type)


def test_project_dashboard_is_valid() -> None:
    """The dashboard of the project loads with known panel types and shows the live X-ray collection."""
    config = load_dashboard_config(PROJECT_CONFIG / "dashboard", PROJECT_CONFIG / "feeds.toml")

    assert {panel.type for panel in config.panel} <= PANEL_TYPES.keys()
    assert "swpc_goes_xray" in config.collections()


def test_panels_are_validated_by_their_type_and_grouped_into_rows(tmp_path: pathlib.Path) -> None:
    """Each panel comes back as the model of its type, and rows are sorted by number with the panels in file order."""
    content = (
        'title = "Sun"\n'
        + VALUE_PANEL.replace("row = 1", "row = 2")
        + 'title = "first"\n'
        + STACK_PANEL
        + 'title = "top"\n'
        + VALUE_PANEL.replace("row = 1", "row = 2")
        + 'title = "second"\n'
    )

    config = load_dashboard_config(*_write(tmp_path, {"sun": content}))

    assert [(title, [[(type(panel), panel.title) for panel in row] for row in rows]) for title, rows in config.pages()] == [
        ("Sun", [[(StackPanel, "top")], [(ValuePanel, "first"), (ValuePanel, "second")]])
    ]


def test_pages_follow_the_page_order_and_name_their_panels(tmp_path: pathlib.Path) -> None:
    """Pages keep the order of the page order file, and each panel takes the title of its page file as its page."""
    pages = {"earth": 'title = "Earth"\n' + VALUE_PANEL + 'title = "kp"\n', "sun": 'title = "Sun"\n' + STACK_PANEL + 'title = "flux"\n'}

    config = load_dashboard_config(*_write(tmp_path, pages))

    assert [(title, [[(panel.page, panel.title) for panel in row] for row in rows]) for title, rows in config.pages()] == [
        ("Earth", [[("Earth", "kp")]]),
        ("Sun", [[("Sun", "flux")]]),
    ]


@pytest.mark.parametrize(
    "pages",
    [
        pytest.param({"sun": 'title = "Sun"\n[[panel]]\ntype = "gauge"\nrow = 1\n'}, id="unknown-panel-type"),
        pytest.param({"sun": 'title = "Sun"\n' + VALUE_PANEL.replace("a_set", "unfed")}, id="dataset-of-no-feed"),
        pytest.param({"sun": 'title = "Sun"\n' + VALUE_PANEL + 'feild = "flux"\n'}, id="unknown-key"),
        pytest.param({"sun": 'title = "Sun"\n' + VALUE_PANEL + 'scale = "noaa_x"\n'}, id="unknown-scale"),
        pytest.param(
            {"sun": 'title = "Sun"\n' + VALUE_PANEL + 'scale = "noaa_g"\nthresholds = [{ value = 1.0, label = "high" }]\n'}, id="scale-and-thresholds"
        ),
        pytest.param({"sun": 'title = "Sun"\n' + STACK_PANEL.replace("P7D", "PT1H")}, id="history-shorter-than-view"),
        pytest.param(
            {"sun": 'title = "Sun"\n' + STACK_PANEL.replace("rows = [{ series", 'rows = [{ y_axis = "log", y_range = [0, 10], series')},
            id="log-axis-with-non-positive-range",
        ),
        pytest.param({"sun": 'title = "Sun"\n' + VALUE_PANEL + 'format = "{value}"\n'}, id="no-format-string"),
        pytest.param({"sun": 'title = "Sun"\n' + VALUE_PANEL + 'page = "Earth"\n'}, id="panel-naming-a-page"),
        pytest.param({"sun": 'title = "Sun"\n', "sun_again": 'title = "Sun"\n'}, id="repeated-page-title"),
        pytest.param({"sun": VALUE_PANEL}, id="page-without-title"),
    ],
)
def test_invalid_dashboard_is_rejected(tmp_path: pathlib.Path, pages: dict[str, str]) -> None:
    """Unknown types, keys and scales, datasets of no feed, contradicting or unusable settings and unusable pages fail validation."""
    with pytest.raises(ValidationError):
        load_dashboard_config(*_write(tmp_path, pages))


def test_a_registered_panel_type_validates_its_own_settings() -> None:
    """A new panel type needs only its model and a registry entry to validate tables of its type."""
    panel_types = {"gauge": PanelType(GaugePanel, _build_nothing)}

    panel = panel_from_table({"type": "gauge", "row": 1, "needle": 0.5}, panel_types)

    assert panel == GaugePanel(type="gauge", row=1, needle=0.5)


def test_feed_intervals_take_the_shortest_interval_of_each_dataset(tmp_path: pathlib.Path) -> None:
    """Each dataset gets the shortest interval of its feeds, None only if all its feeds load on start."""
    content = (
        '[[feed]]\nname = "hourly"\ndatasets = ["shared_set"]\ninterval = "PT1H"\n\n'
        '[[feed]]\nname = "start_only"\ndatasets = ["start_set", "mixed_set"]\n\n'
        '[[feed]]\nname = "minutely"\ndatasets = ["shared_set", "mixed_set"]\ninterval = "PT1M"\n'
    )

    _, feeds_file = _write(tmp_path, {}, content)

    assert load_feed_intervals(feeds_file) == {
        "shared_set": timedelta(minutes=1),
        "start_set": None,
        "mixed_set": timedelta(minutes=1),
    }
