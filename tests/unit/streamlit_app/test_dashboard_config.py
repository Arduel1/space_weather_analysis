import pathlib
from datetime import timedelta

import pytest
from pydantic import ValidationError

from streamlit_app.dashboard.config import load_dashboard_config

PROJECT_FEED_FILE = pathlib.Path(__file__).parents[3] / "config" / "feeds.toml"
FEED = '[[feed]]\nname = "live"\ndatasets = ["a_set", "b_set"]\n\n'


def _write(directory: pathlib.Path, content: str) -> pathlib.Path:
    """Writes a feed file into a directory and returns its path."""
    path = directory / "feeds.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_project_dashboard_table_is_valid() -> None:
    """The dashboard table of the project feed file loads and shows only datasets of its feeds."""
    config = load_dashboard_config(PROJECT_FEED_FILE)

    assert config.charts.xray is not None and config.charts.xray.collection == "swpc_goes_xray"


def test_dashboard_table_reads_references_and_windows(tmp_path: pathlib.Path) -> None:
    """Datasets name their collections, view and history are ISO 8601 durations, and parts that are not configured stay empty."""
    content = FEED + '[dashboard.charts.protons]\ndataset = "a_set"\nfield = "flux"\nview = "PT24H"\nhistory = "P7D"\n'

    config = load_dashboard_config(_write(tmp_path, content))

    protons = config.charts.protons
    assert protons is not None
    assert (protons.collection, protons.field, protons.view, protons.history, config.charts.kp, config.collections()) == (
        "a_set",
        "flux",
        timedelta(hours=24),
        timedelta(days=7),
        None,
        {"a_set"},
    )


@pytest.mark.parametrize(
    "dashboard",
    [
        pytest.param('[dashboard.tiles.xray]\ndataset = "unfed"\nfield = "flux"\n', id="dataset-of-no-feed"),
        pytest.param('[dashboard.tiles.xray]\ndataset = "a_set"\n', id="missing-field"),
        pytest.param('[dashboard.charts.protons]\ndataset = "a_set"\nfield = "flux"\nview = "24 hours"\nhistory = "P7D"\n', id="no-iso-duration"),
        pytest.param(
            '[dashboard.charts.protons]\ndataset = "a_set"\nfield = "flux"\nview = "P7D"\nhistory = "PT24H"\n', id="history-shorter-than-view"
        ),
        pytest.param('[dashboard.tiles.weather]\ndataset = "a_set"\nfield = "flux"\n', id="unknown-tile"),
    ],
)
def test_invalid_dashboard_table_is_rejected(tmp_path: pathlib.Path, dashboard: str) -> None:
    """Datasets no feed loads, missing fields, malformed or contradicting windows and unknown parts fail validation."""
    with pytest.raises(ValidationError):
        load_dashboard_config(_write(tmp_path, FEED + dashboard))
