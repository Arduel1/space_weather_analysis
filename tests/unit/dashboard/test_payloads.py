import pathlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from site_export.main import main
from space_weather_analysis.dashboard.labels import Metadata
from space_weather_analysis.dashboard.panels.base import PanelContext
from space_weather_analysis.dashboard.panels.feeds import FeedsPanel, build_feeds
from space_weather_analysis.dashboard.panels.table import TablePanel, build_table
from space_weather_analysis.dashboard.panels.timeseries import TimeseriesPanel, build_timeseries, reduce_points
from space_weather_analysis.dashboard.panels.value import ValuePanel, build_value
from space_weather_analysis.jobs.models import ChunkGap, FeedJobSummary, Job, JobStatus

NOW = datetime(2024, 1, 1, 12, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
# A field with a declared fill value, published every minute
FLUX_RECORD: Metadata = {
    "time": {"cadence": "PT1M"},
    "variables": [{"name": "flux", "label": "Flux", "units": "pfu", "fill": "-1"}],
}


class FakeReader:
    """Serves fixed frames, metadata records and feed summaries in place of the database."""

    def __init__(self, frames: Mapping[str, pd.DataFrame], metadata: Mapping[str, Metadata], summaries: list[FeedJobSummary] | None = None) -> None:
        """Keeps the frames and records by dataset ID and the feed summaries."""
        self._frames = frames
        self._metadata = metadata
        self._summaries = summaries or []

    def latest(self, dataset: str, fields: tuple[str, ...]) -> tuple[datetime, dict[str, Any]] | None:
        """Returns the last row of the frame of a dataset; None without rows."""
        frame = self._frames.get(dataset, pd.DataFrame())
        if frame.empty:
            return None
        return frame.index[-1].to_pydatetime(), {field: frame[field].iloc[-1] for field in fields if field in frame.columns}

    def recent(self, dataset: str, span: timedelta, fields: tuple[str, ...]) -> pd.DataFrame:
        """Returns the whole frame of a dataset; the tests keep their frames inside every span."""
        del span, fields
        return self._frames.get(dataset, pd.DataFrame())

    def metadata(self, dataset: str) -> Metadata:
        """Returns the record of a dataset; empty if it has none."""
        return self._metadata.get(dataset, {})

    def feed_summaries(self, since: datetime) -> list[FeedJobSummary]:
        """Returns the fixed summaries."""
        del since
        return self._summaries


def _minutes(*minutes: int) -> pd.DatetimeIndex:
    """Provides time stamps the given minutes before NOW."""
    return pd.DatetimeIndex([NOW - minute * MINUTE for minute in minutes])


CONTEXT = PanelContext(now=NOW, feed_intervals={"flux_set": MINUTE}, feed_schedule={"live": MINUTE})


def test_reduce_points_keeps_peaks_and_line_breaks() -> None:
    """A long series keeps its extremes and the first value of each missing run within the limit; a short one stays unchanged."""
    values = pd.Series(np.sin(np.arange(1000) / 50.0), index=pd.date_range(NOW, periods=1000, freq="min"))
    values.iloc[500] = 10.0
    values.iloc[700:710] = np.nan

    reduced = reduce_points(values, max_points=100)
    short = reduce_points(values.iloc[:100], max_points=100)

    assert (len(reduced) <= 101, reduced.max(), reduced.min(), int(reduced.isna().sum()), reduced.index.is_monotonic_increasing) == (
        True,
        10.0,
        values.min(),
        1,
        True,
    )
    pd.testing.assert_series_equal(short, values.iloc[:100])


@pytest.mark.parametrize(
    ("frames", "expected_text", "expected_freshness"),
    [
        pytest.param(
            {"flux_set": pd.DataFrame({"flux": [5.0]}, index=_minutes(2))},
            "5.0 pfu",
            [{"dataset": "flux_set", "newest": "2024-01-01T11:58:00Z", "expected_age": 1020.0}],
            id="stored-value",
        ),
        pytest.param({}, "n/a", [], id="no-data"),
    ],
)
def test_value_payload_carries_the_view_and_the_expected_age(
    frames: dict[str, pd.DataFrame], expected_text: str, expected_freshness: list[dict[str, Any]]
) -> None:
    """The tile payload holds the formatted value and, with data, the newest time with its expected age in seconds."""
    panel = ValuePanel(type="value", row=1, dataset="flux_set", field="flux", format="{:.1f}")

    payload = build_value(panel, FakeReader(frames, {"flux_set": FLUX_RECORD}), CONTEXT).model_dump(mode="json")

    assert (payload["view"]["text"], payload["freshness"]) == (expected_text, expected_freshness)


def test_timeseries_payload_keeps_invalid_values_and_breaks_the_line_at_gaps() -> None:
    """Fill values stay in the payload with the valid range beside them, a gap gets one null, and durations are seconds."""
    panel = TimeseriesPanel.model_validate(
        {"type": "timeseries", "row": 1, "view": "PT1H", "history": "PT1H", "scale": "noaa_s", "series": [{"dataset": "flux_set", "field": "flux"}]}
    )
    frame = pd.DataFrame({"flux": [1.0, -1.0, 3.0, 4.0]}, index=_minutes(20, 19, 18, 2))

    payload = build_timeseries(panel, FakeReader({"flux_set": frame}, {"flux_set": FLUX_RECORD}), CONTEXT).model_dump(mode="json")

    series = payload["series"][0]
    assert (payload["view"], len(payload["levels"]), series["label"], series["valid"], series["values"], series["times"][3]) == (
        3600.0,
        5,
        "Flux",
        {"fill": -1.0, "low": None, "high": None},
        [1.0, -1.0, 3.0, None, 4.0],
        "2024-01-01T11:50:00Z",
    )


@pytest.mark.parametrize(
    ("frames", "expected_rows"),
    [
        pytest.param(
            {"cme_set": pd.DataFrame({"speed": [400.0, np.nan], "halo": [False, True]}, index=_minutes(30, 10))},
            [["2024-01-01T11:50:00Z", None, True], ["2024-01-01T11:30:00Z", 400.0, False]],
            id="newest-first",
        ),
        pytest.param({}, [], id="no-data"),
    ],
)
def test_table_payload_lists_the_newest_rows_under_every_heading(frames: dict[str, pd.DataFrame], expected_rows: list[list[Any]]) -> None:
    """Rows come newest first with ISO times and null for missing values; without data the headings stay."""
    panel = TablePanel.model_validate(
        {"type": "table", "row": 1, "dataset": "cme_set", "history": "P1D", "columns": [{"field": "speed", "label": "Speed"}, {"field": "halo"}]}
    )

    payload = build_table(panel, FakeReader(frames, {}), CONTEXT).model_dump(mode="json")

    assert (payload["columns"], payload["rows"]) == (["Time (UTC)", "Speed", "halo"], expected_rows)


def test_feeds_payload_rates_each_feed_and_names_its_problem() -> None:
    """Each feed gets its state, counts and the causes of the missing chunks of its newest job."""
    gap = ChunkGap(dataset_id="flux_set", start=NOW - MINUTE, stop=NOW, reason="SourceUnreachableError: no answer")
    latest = Job(id="0" * 24, status=JobStatus.COMPLETED, created_at=NOW - MINUTE, missing_chunks=[gap])
    summary = FeedJobSummary(feed="live", latest=latest, runs=3, completed=2, with_gaps=1, failed=0)

    payload = build_feeds(FeedsPanel(type="feeds", row=1), FakeReader({}, {}, [summary]), CONTEXT).model_dump(mode="json")

    assert payload["feeds"] == [
        {
            "feed": "live",
            "state": "gaps",
            "last_run": "2024-01-01T11:59:00Z",
            "interval": 60.0,
            "runs": 3,
            "completed": 2,
            "with_gaps": 1,
            "failed": 0,
            "problem": "Missing chunks: 1 source unreachable",
        }
    ]


def test_export_exits_with_an_invalid_feed_file(tmp_path: pathlib.Path) -> None:
    """An unreadable dashboard table ends the export with status 1 before the database is contacted, writing nothing."""
    feeds_file = tmp_path / "feeds.toml"
    feeds_file.write_text('[[dashboard.panel]]\ntype = "gauge"\nrow = 1\n', encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main(["--output", str(tmp_path / "site"), "--feeds-file", str(feeds_file)])

    assert (exit_info.value.code, (tmp_path / "site").exists()) == (1, False)
