import json
import pathlib
from datetime import UTC, datetime

import pandas as pd
import pytest
from pymongo.database import Database

from site_export.main import main
from space_weather_analysis.config.mongo import MongoSettings
from space_weather_analysis.timeseries.documents import frame_to_documents
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository

FEEDS = """
[[feed]]
name = "live"
datasets = ["synthetic_flux"]
interval = "PT1M"
"""
PAGE = """
title = "Live"

[[panel]]
type = "value"
row = 1
dataset = "synthetic_flux"
field = "flux"

[[panel]]
type = "stack"
row = 1
view = "PT1H"
history = "PT1H"
rows = [{ series = [{ dataset = "synthetic_flux", field = "flux" }] }]

[[panel]]
type = "feeds"
row = 2
"""


class _FixedDatetime(datetime):
    """datetime whose now is one fixed time, so that two exports see the same time."""

    @classmethod
    def now(cls, tz: object = None) -> _FixedDatetime:
        return cls(2024, 1, 1, 0, 10, tzinfo=UTC)


@pytest.fixture
def export_arguments(db: Database, mongo_settings: MongoSettings, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stores a synthetic dataset, writes a feed file, a one-page dashboard and a web frontend, and returns the export arguments."""
    repository = MongoTimeSeriesRepository(db)
    repository.ensure_collection("synthetic_flux", Granularity.MINUTES)
    frame = pd.DataFrame({"flux": [1.0, 2.0, 3.0]}, index=pd.date_range("2024-01-01", periods=3, freq="min", tz="UTC"))
    repository.insert("synthetic_flux", frame_to_documents(frame, "LOCAL/SYNTHETIC"))
    monkeypatch.setenv("MONGO_USER", mongo_settings.user)
    monkeypatch.setenv("MONGO_PASS", mongo_settings.password.get_secret_value())
    monkeypatch.setenv("MONGO_HOST", mongo_settings.host)
    monkeypatch.setenv("MONGO_PORT", str(mongo_settings.port))
    monkeypatch.setenv("MONGO_DB_NAME", mongo_settings.db_name)
    feeds_file = tmp_path / "feeds.toml"
    feeds_file.write_text(FEEDS, encoding="utf-8")
    dashboard_dir = tmp_path / "dashboard"
    dashboard_dir.mkdir()
    (dashboard_dir / "pages.toml").write_text('pages = ["live"]\n', encoding="utf-8")
    (dashboard_dir / "live.toml").write_text(PAGE, encoding="utf-8")
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return ["--output", str(tmp_path / "site"), "--web", str(web), "--feeds-file", str(feeds_file), "--dashboard-dir", str(dashboard_dir)]


@pytest.mark.integration
def test_export_writes_the_layout_and_one_document_per_panel(export_arguments: list[str], tmp_path: pathlib.Path) -> None:
    """The export copies the web frontend into the site and writes a layout naming each panel document it writes next to it."""
    output = tmp_path / "site"

    main(export_arguments)

    layout = json.loads((output / "data" / "dashboard.json").read_text(encoding="utf-8"))
    panel_ids = [entry["id"] for page in layout["pages"] for row in page["rows"] for entry in row]
    value = json.loads((output / "data" / "panels" / f"{panel_ids[0]}.json").read_text(encoding="utf-8"))
    assert (
        (output / "index.html").exists(),
        panel_ids,
        sorted(path.name for path in (output / "data" / "panels").iterdir()),
        value["view"]["text"],
    ) == (True, ["panel-0-0-0", "panel-0-0-1", "panel-0-1-0"], ["panel-0-0-0.json", "panel-0-0-1.json", "panel-0-1-0.json"], "3")


@pytest.mark.integration
def test_second_export_without_changes_leaves_the_panel_documents_untouched(
    export_arguments: list[str], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the same data and time, a second export writes only the layout and keeps every panel document as it was."""
    monkeypatch.setattr("site_export.main.datetime", _FixedDatetime)
    panels_dir = tmp_path / "site" / "data" / "panels"
    main(export_arguments)
    first_times = {path.name: path.stat().st_mtime_ns for path in panels_dir.iterdir()}

    main(export_arguments)

    assert {path.name: path.stat().st_mtime_ns for path in panels_dir.iterdir()} == first_times
