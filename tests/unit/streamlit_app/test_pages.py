from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from space_weather_analysis.catalog.models import MasterCatalogItem
from streamlit_app import data

APP_DIR = Path(data.__file__).parent
COLLECTION = "synthetic_values"
DATASET_ID = "synthetic/values"
CATALOG: list[MasterCatalogItem] = [
    {
        "dataset_id": DATASET_ID,
        "sources": [
            {
                "type": "HAPI",
                "source_id": "https://hapi.invalid/hapi",
                "time_range": {"start_time": "2024-01-01T00:00:00Z", "end_time": "2024-01-08T00:00:00Z"},
            }
        ],
    }
]


def _values_frame(*_: Any) -> pd.DataFrame:
    """Returns two rows of one numeric field with an aware UTC index, like a read of the time series repository."""
    index = pd.DatetimeIndex(pd.to_datetime(["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"], utc=True), name="timestamp")
    return pd.DataFrame({"value": [1.0, 2.0]}, index=index)


def _feed_frame() -> pd.DataFrame:
    """Returns two rows holding every field the dashboard charts and table read, so any panel can be drawn from it."""
    return (
        _values_frame()
        .assign(Kp=3.0, estimated_kp=4.0, long_flux=2e-6, short_flux=1e-7, p_ge10mev_flux=0.5, linear_speed=500.0, remarks="synthetic")
        .drop(columns="value")
    )


def _run(page: str) -> AppTest:
    """Runs a script of the app once and checks that it raised no exception."""
    app = AppTest.from_file(str(APP_DIR / page)).run()
    assert not app.exception
    return app


def test_main_shows_the_dashboard_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a database, the entry script opens the dashboard page."""
    monkeypatch.setattr(data, "get_repositories", object)
    monkeypatch.setattr(data, "read_latest", lambda *_: None)
    monkeypatch.setattr(data, "read_recent", lambda *_: pd.DataFrame())
    monkeypatch.setattr(data, "read_newest_time", lambda _: None)
    monkeypatch.setattr(data, "get_feed_summaries", lambda _: [])
    monkeypatch.setattr(data, "get_metadata", lambda _: {})

    app = _run("main.py")

    assert [title.value for title in app.title] == ["Space Weather Dashboard"]


def test_main_without_database_shows_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a database, the entry script shows an error instead of a page."""
    monkeypatch.setattr(data, "get_repositories", lambda: None)

    app = _run("main.py")

    assert len(app.error) == 1
    assert not app.title


def test_dashboard_shows_tiles_charts_and_notes_for_missing_feeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured datasets with documents get their tile value, chart and table; one without documents gets a note."""
    latest_time = datetime(2024, 1, 1, 1, tzinfo=UTC)
    newest = {
        "Kp": {"Kp": 3.0},
        "swpc_kp_estimated": {"estimated_kp": 4.0},
        "swpc_goes_xray": {"long_flux": 2e-6},
        "swpc_goes_protons": {"p_ge10mev_flux": 0.5},
        "silso_sunspot_daily": {"sunspot_number": 120.0},
        "cme_catalog": {"linear_speed": 500.0},
    }
    monkeypatch.setattr(data, "read_latest", lambda collection, _: (latest_time, newest[collection]) if collection in newest else None)
    monkeypatch.setattr(data, "read_recent", lambda collection, *_: _feed_frame() if collection in newest else pd.DataFrame())
    monkeypatch.setattr(data, "read_newest_time", lambda _: latest_time)
    monkeypatch.setattr(data, "get_feed_summaries", lambda _: [])
    monkeypatch.setattr(data, "get_metadata", lambda _: {"source": {"type": "SWPC", "provider": "Synthetic provider"}})

    app = _run("pages/dashboard.py")

    assert [metric.value for metric in app.metric] == ["4.00", "C2.0", "5.00e-01 pfu", "120"]
    # Only the Kp chart has a valid range, configured in the project feed file
    assert [toggle.label for toggle in app.toggle] == ["Live updates", "Hide invalid values"]
    # Notes: the sunspot chart without data and the feed status without jobs
    assert (len(app.get("plotly_chart")), len(app.dataframe), len(app.info)) == (3, 1, 2)
    assert [caption.value.split(" -- ")[-1] for caption in app.caption if "Source:" in caption.value] == ["Source: Synthetic provider"] * 8
    assert [caption.value.split(" newest value")[0] for caption in app.caption if " UTC, " in caption.value] == [
        "2024-01-01 01:00 UTC, :red[stale],"
    ] * 3 + ["2024-01-01 01:00 UTC, :gray[static],"]


def test_explorer_shows_the_selected_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    """The explorer offers the stored collections and plots the first one with its metadata and raw table."""
    monkeypatch.setattr(data, "list_stored_collections", lambda: [COLLECTION])
    monkeypatch.setattr(data, "read_collection_range", _values_frame)
    monkeypatch.setattr(data, "get_metadata_for_collection", lambda _: {"dataset_id": DATASET_ID})

    app = _run("pages/explorer.py")

    assert [title.value for title in app.main.title] == [f"Analysis: {COLLECTION}"]
    assert app.sidebar.selectbox[0].options == [COLLECTION]
    assert [expander.label for expander in app.expander] == ["Dataset Metadata", "Raw Data Table"]


def test_explorer_without_stored_data_shows_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without stored collections, the explorer asks for a pipeline run."""
    monkeypatch.setattr(data, "list_stored_collections", list)

    app = _run("pages/explorer.py")

    assert len(app.warning) == 1


def test_download_page_offers_the_catalog_datasets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The download page offers the catalog datasets and the job button without a running job."""
    monkeypatch.setattr(data, "get_master_catalog", lambda: CATALOG)
    monkeypatch.setattr(data, "get_metadata", lambda _: {})

    app = _run("pages/download.py")

    assert app.selectbox[0].options == [DATASET_ID]
    assert [button.label for button in app.button] == ["Start Download Job"]
    assert not app.error
