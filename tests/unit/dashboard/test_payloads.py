import pathlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from site_export.main import main
from space_weather_analysis.clients.image_sequences import DIFFERENCE_FILE_FIELD, DISK_FIELDS, FRAME_FILE_FIELD, ZOOM_EVENT_FIELDS
from space_weather_analysis.dashboard import export
from space_weather_analysis.dashboard.config import DashboardConfig
from space_weather_analysis.dashboard.export import build_dashboard
from space_weather_analysis.dashboard.labels import Metadata
from space_weather_analysis.dashboard.panel_types import PanelType
from space_weather_analysis.dashboard.panels.aurora import AuroraPanel, build_aurora
from space_weather_analysis.dashboard.panels.base import PanelContext
from space_weather_analysis.dashboard.panels.chains import ChainsPanel, build_chains
from space_weather_analysis.dashboard.panels.cycles import find_minima
from space_weather_analysis.dashboard.panels.events import EpochPanel, build_epoch, find_events
from space_weather_analysis.dashboard.panels.feeds import FeedsPanel, build_feeds, dataset_problem
from space_weather_analysis.dashboard.panels.heliosphere import HeliospherePanel, build_heliosphere
from space_weather_analysis.dashboard.panels.images import ImagesPanel, build_images
from space_weather_analysis.dashboard.panels.magnetic import MagneticFieldPanel, build_magnetic_field
from space_weather_analysis.dashboard.panels.magnetic_connection import MagneticConnectionPanel, build_magnetic_connection
from space_weather_analysis.dashboard.panels.relations import GroupedPanel, build_grouped
from space_weather_analysis.dashboard.panels.series import reduce_points, resample, shift_values
from space_weather_analysis.dashboard.panels.solar_cycle import SolarCyclePanel, build_solar_cycle
from space_weather_analysis.dashboard.panels.timeseries import StackPanel, build_stack
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


def test_stack_payload_keeps_invalid_values_and_breaks_the_line_at_gaps() -> None:
    """Fill values stay in the payload with the valid range beside them, a gap gets one null, and durations are seconds."""
    panel = StackPanel.model_validate(
        {
            "type": "stack",
            "row": 1,
            "view": "PT1H",
            "history": "PT1H",
            "rows": [{"scale": "noaa_s", "series": [{"dataset": "flux_set", "field": "flux"}]}],
        }
    )
    frame = pd.DataFrame({"flux": [1.0, -1.0, 3.0, 4.0]}, index=_minutes(20, 19, 18, 2))

    payload = build_stack(panel, FakeReader({"flux_set": frame}, {"flux_set": FLUX_RECORD}), CONTEXT).model_dump(mode="json")

    row = payload["rows"][0]
    series = row["series"][0]
    assert (payload["view"], len(row["levels"]), series["label"], series["valid"], series["values"], series["times"][3]) == (
        3600.0,
        5,
        "Flux",
        {"fill": -1.0, "low": None, "high": None},
        [1.0, -1.0, 3.0, None, 4.0],
        "2024-01-01T11:50:00Z",
    )


def test_data_updates_rate_each_dataset_of_a_feed_on_its_own() -> None:
    """Only the dataset missing chunks counts as gaps with its cause; each dataset carries its title and its newest value."""
    gap = ChunkGap(dataset_id="flux_set", start=NOW - MINUTE, stop=NOW, reason="SourceUnreachableError: no answer")
    latest = Job(id="0" * 24, status=JobStatus.COMPLETED, created_at=NOW - MINUTE, missing_chunks=[gap])
    summary = FeedJobSummary(feed="live", latest=latest, runs=3, completed=2, with_gaps=1, failed=0)
    context = PanelContext(now=NOW, feed_intervals={}, feed_schedule={"live": MINUTE}, feed_datasets={"live": ("flux_set", "other_set")})
    frames = {"flux_set": pd.DataFrame({"flux": [1.0]}, index=_minutes(5))}
    reader = FakeReader(frames, {"flux_set": {**FLUX_RECORD, "title": "Proton flux"}}, [summary])

    payload = build_feeds(FeedsPanel(type="feeds", row=1), reader, context)

    assert [(status.title, status.state, status.problem, status.freshness is not None) for status in payload.datasets] == [
        ("Proton flux", "gaps", "Missing parts: 1 provider did not answer", True),
        ("other_set", "ok", "", False),
    ]


def test_data_updates_name_a_fault_of_this_site_without_its_internals() -> None:
    """A broken job shows as a processing error on this site, without the exception text meant for the logs."""
    latest = Job(id="0" * 24, status=JobStatus.FAILED, created_at=NOW - MINUTE, error="KeyError: 'X'")
    summary = FeedJobSummary(feed="live", latest=latest, runs=1, completed=0, with_gaps=0, failed=1)

    assert dataset_problem(summary, "flux_set") == "Failed: processing error on this site"


def test_export_exits_with_an_invalid_dashboard(tmp_path: pathlib.Path) -> None:
    """An invalid page file ends the export with status 1 before the database is contacted, writing nothing."""
    feeds_file = tmp_path / "feeds.toml"
    feeds_file.write_text("", encoding="utf-8")
    dashboard_dir = tmp_path / "dashboard"
    dashboard_dir.mkdir()
    (dashboard_dir / "pages.toml").write_text('pages = ["overview"]\n', encoding="utf-8")
    (dashboard_dir / "overview.toml").write_text('title = "Overview"\n[[panel]]\ntype = "gauge"\nrow = 1\n', encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main(["--output", str(tmp_path / "site"), "--feeds-file", str(feeds_file), "--dashboard-dir", str(dashboard_dir)])

    assert (exit_info.value.code, (tmp_path / "site").exists()) == (1, False)


def test_every_panel_carries_the_metadata_of_its_shown_fields() -> None:
    """The export adds the record of each dataset with the shown fields in panel order, and leaves out a dataset without record."""
    content = {
        "page": [],
        "panel": [
            {
                "type": "stack",
                "row": 1,
                "view": "PT1H",
                "history": "PT1H",
                "rows": [
                    {
                        "series": [
                            {"dataset": "flux_set", "field": "flux"},
                            {"dataset": "flux_set", "field": "other"},
                            {"dataset": "unknown_set", "field": "flux"},
                        ]
                    }
                ],
            }
        ],
    }
    frames = {"flux_set": pd.DataFrame({"flux": [1.0], "other": [2.0]}, index=_minutes(1))}

    _, payloads = build_dashboard(DashboardConfig.model_validate(content), FakeReader(frames, {"flux_set": FLUX_RECORD}), CONTEXT)

    about = payloads["panel-0-0-0"].model_dump(mode="json")["about"]
    assert [
        (entry["dataset"], entry["cadence"], [(field["name"], field["units"], field["validity"]) for field in entry["fields"]]) for entry in about
    ] == [("flux_set", 60.0, [("flux", "pfu", "fill -1"), ("other", None, None)])]


def test_panels_share_the_footnote_of_a_source_and_number_formulas_too() -> None:
    """A source shown by two panels gets one number for both, and the publication of a computed formula gets its own."""
    plasma = {"dataset": "flux_set", "field": "flux"}
    content = {
        "page": [],
        "panel": [
            {"type": "stack", "row": 1, "view": "PT1H", "history": "PT1H", "rows": [{"series": [plasma]}]},
            {
                "type": "stack",
                "row": 2,
                "view": "PT1H",
                "history": "PT1H",
                "rows": [{"series": [plasma, {"derived": "l1_travel_time", "inputs": {"speed": plasma}}]}],
            },
        ],
    }
    frames = {"flux_set": pd.DataFrame({"flux": [400.0]}, index=_minutes(1))}

    layout, _ = build_dashboard(DashboardConfig.model_validate(content), FakeReader(frames, {"flux_set": FLUX_RECORD}), CONTEXT)

    footnotes = [entry.credits for page in layout.pages for row in page.rows for entry in row]
    assert (footnotes, [credit.source for credit in layout.credits][1]) == ([[0], [0, 1]], "Distance L1 to Earth about 1.5 million km")


def test_a_panel_whose_build_fails_gets_a_failure_document_while_the_others_are_built(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fault in one panel type leaves its panel with the reason and the other panels with their data."""

    def broken(panel: Any, reader: Any, context: Any) -> Any:
        raise ValueError("no frames")

    panel_types = dict(export.PANEL_TYPES)
    panel_types["feeds"] = PanelType(FeedsPanel, broken)
    monkeypatch.setattr(export, "PANEL_TYPES", panel_types)
    content = {
        "page": [],
        "panel": [
            {"type": "feeds", "row": 1},
            {"type": "stack", "row": 2, "view": "PT1H", "history": "PT1H", "rows": [{"series": [{"dataset": "flux_set", "field": "flux"}]}]},
        ],
    }
    frames = {"flux_set": pd.DataFrame({"flux": [1.0]}, index=_minutes(1))}

    _, payloads = build_dashboard(DashboardConfig.model_validate(content), FakeReader(frames, {"flux_set": FLUX_RECORD}), CONTEXT)

    assert (payloads["panel-0-0-0"].model_dump(mode="json")["error"]["message"], payloads["panel-0-1-0"].type) == (
        "ValueError: no frames",
        "stack",
    )


def test_an_estimate_continues_only_after_the_newest_measured_value() -> None:
    """A continuing series keeps only its values after the newest value of the series before it in the row."""
    measured = pd.DataFrame({"kp": [3.0, 4.0, np.nan]}, index=_minutes(300, 120, 60))
    estimated = pd.DataFrame({"kp": [3.3, 4.3, 5.0]}, index=_minutes(200, 100, 30))
    panel = StackPanel.model_validate(
        {
            "type": "stack",
            "row": 1,
            "view": "P1D",
            "history": "P1D",
            "rows": [
                {"series": [{"dataset": "measured", "field": "kp"}, {"dataset": "estimated", "field": "kp", "continues": True, "hatched": True}]}
            ],
        }
    )

    payload = build_stack(panel, FakeReader({"measured": measured, "estimated": estimated}, {}), CONTEXT)

    continued = payload.rows[0].series[1]
    assert (continued.values, continued.hatched) == ([4.3, 5.0], True)


def test_resampling_counts_the_values_above_a_bound_per_calendar_year() -> None:
    """With a bound, each calendar year gets the number of its values above the bound, starting on January 1."""
    values = pd.Series(
        [10.0, 60.0, 70.0, 20.0, 80.0], index=pd.DatetimeIndex(["2020-03-01", "2020-07-01", "2020-12-31", "2021-02-01", "2021-05-01"], tz="UTC")
    )

    counted = resample(values, timedelta(days=365), "mean", above=50)

    assert counted.to_dict() == {pd.Timestamp("2020-01-01", tz="UTC"): 2.0, pd.Timestamp("2021-01-01", tz="UTC"): 1.0}


def test_resample_of_a_field_without_values_gives_no_steps() -> None:
    """A field the dataset has not stored yet arrives without time index and resamples to an empty series."""
    combined = resample(pd.Series(dtype=float), timedelta(hours=1), "max", None)

    assert combined.empty


def test_shift_field_moves_each_value_by_its_days() -> None:
    """A value is moved by the days of its own document, and a value without shift is left out."""
    frame = pd.DataFrame({"speed": [500.0, 600.0], "lag": [-2.0, np.nan]}, index=pd.DatetimeIndex([NOW, NOW + MINUTE]))

    moved = shift_values(frame["speed"], frame, "lag")

    assert list(moved.items()) == [(pd.Timestamp(NOW - timedelta(days=2)), 500.0)]


def test_events_join_close_stretches_beyond_the_threshold_and_keep_their_extreme() -> None:
    """Values beyond the threshold with gaps up to the separation form one event at its most extreme value."""
    times = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    values = pd.Series([0.0, -120.0, -150.0, 0.0, -110.0, 0.0, 0.0, 0.0, 0.0, -200.0], index=times)

    events = find_events(values, threshold=-100, below=True, separation=timedelta(days=2))

    assert events.to_dict() == {times[2]: -150.0, times[9]: -200.0}


def test_cycle_minima_are_found_only_with_a_complete_neighbourhood() -> None:
    """A minimum needs the separation of values around it, so lows at both ends of the series do not count."""
    months = pd.date_range("1900-01-01", periods=12 * 40, freq="MS", tz="UTC")
    # A cycle of eleven years whose minima fall into 1900, 1911, 1922, 1933 and the end of the series
    values = pd.Series(100 - 100 * np.cos(2 * np.pi * np.arange(len(months)) / (12 * 11)), index=months)

    minima = find_minima(values, timedelta(days=8 * 365.2425))

    assert [time.year for time in minima] == [1911, 1922, 1933]


def _storm_frame() -> pd.DataFrame:
    """Provides hourly data of thirty quiet days with two storms whose Dst falls to its lowest at 12:00 of days 10 and 20."""
    hours = pd.date_range("2024-01-01", periods=30 * 24, freq="h", tz="UTC")
    dst = pd.Series(0.0, index=hours)
    speed = pd.Series(400.0, index=hours)
    for day, depth in ((10, -150.0), (20, -300.0)):
        lowest = pd.Timestamp("2024-01-01", tz="UTC") + timedelta(days=day, hours=12)
        dst[lowest - timedelta(hours=2) : lowest + timedelta(hours=2)] = depth / 2
        dst[lowest] = depth
        speed[lowest - timedelta(hours=6) : lowest] = 700.0
    return pd.DataFrame({"dst": dst, "speed": speed})


def test_epoch_aligns_every_event_at_its_extreme() -> None:
    """Both storms line up at offset zero, where the median is the middle of their lowest values, and the newest storm is shown alone."""
    panel = EpochPanel.model_validate(
        {
            "type": "epoch",
            "row": 1,
            "history": "P30D",
            "events": {"dataset": "omni", "field": "dst", "threshold": -100},
            "before": "PT6H",
            "after": "PT6H",
            "rows": [{"dataset": "omni", "field": "speed"}, {"dataset": "omni", "field": "dst"}],
        }
    )

    payload = build_epoch(panel, FakeReader({"omni": _storm_frame()}, {}), CONTEXT)

    zero = payload.offsets.index(0)
    assert (payload.events, payload.rows[1].median[zero], payload.rows[1].newest[zero], payload.rows[0].median[zero - 1]) == (
        2,
        -225.0,
        -300.0,
        700.0,
    )


def test_chains_link_each_pair_once_and_count_the_chains(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Links listed on both events become one pair and one chain, links to unknown events are dropped, and kinds without lane are left out."""
    dictionary = tmp_path / "spase.json"
    dictionary.write_text('{"dictionary": {"SolarFlare": {"definition": "An explosive event."}}}', encoding="utf-8")
    monkeypatch.setenv("SPASE_DICTIONARY_CACHE_PATH", str(dictionary))
    frame = pd.DataFrame(
        {
            "event_type": ["FLR", "CME", "GST", "RBE"],
            "event_id": ["F1", "C1", "G1", "R1"],
            "linked_ids": ["C1", "F1 G1", "C1 X9", ""],
            "label": ["M1.0", "800 km/s", "Kp 7", None],
        },
        index=_minutes(300, 200, 100, 50),
    )
    panel = ChainsPanel.model_validate(
        {
            "type": "chains",
            "row": 1,
            "dataset": "donki",
            "history": "P1D",
            "lanes": [
                {"event_type": "FLR", "term": "SolarFlare"},
                {"event_type": "CME", "term": "CoronalMassEjection"},
                {"event_type": "GST", "term": "GeomagneticStorm"},
            ],
        }
    )

    payload = build_chains(panel, FakeReader({"donki": frame}, {}), CONTEXT)

    assert (len(payload.events), payload.links, payload.chains, payload.lanes[0].label, payload.lanes[0].definition) == (
        3,
        [(0, 1), (1, 2)],
        [[0, 1, 2]],
        "Solar flare",
        "An explosive event.",
    )


def test_only_a_sequence_with_stored_event_texts_carries_an_event() -> None:
    """The reader returns the event fields of every sequence as columns, empty ones must not make a plain sequence a zoom."""
    plain = pd.DataFrame({FRAME_FILE_FIELD: ["a.png"], **{field: [np.nan] for field in ZOOM_EVENT_FIELDS}}, index=_minutes(1))
    zoom = pd.DataFrame({FRAME_FILE_FIELD: ["b.png"], "event_label": ["M1.1"], "event_peak": ["2024-01-01T11:00:00+00:00"]}, index=_minutes(1))
    panel = ImagesPanel.model_validate(
        {"type": "images", "row": 1, "history": "P1D", "variants": [{"dataset": "plain", "label": "Plain"}, {"dataset": "zoom", "label": "Zoom"}]}
    )

    payload = build_images(panel, FakeReader({"plain": plain, "zoom": zoom}, {}), CONTEXT).model_dump(mode="json")

    assert [variant["event"] for variant in payload["variants"]] == [None, {"label": "M1.1", "peak": "2024-01-01T11:00:00+00:00"}]


def test_an_image_variant_shows_the_files_of_its_own_field() -> None:
    """A zoom offers its frames and, as second variant of the same sequence, their difference images."""
    frame = pd.DataFrame(
        {FRAME_FILE_FIELD: ["a.jpg", "b.jpg"], DIFFERENCE_FILE_FIELD: ["a_difference.jpg", "b_difference.jpg"]}, index=_minutes(2, 1)
    )
    panel = ImagesPanel.model_validate(
        {
            "type": "images",
            "row": 1,
            "history": "P1D",
            "columns": 2,
            "variants": [{"dataset": "zoom", "label": "Zoom"}, {"dataset": "zoom", "field": DIFFERENCE_FILE_FIELD, "label": "Change"}],
        }
    )

    payload = build_images(panel, FakeReader({"zoom": frame}, {}), CONTEXT)

    assert [[item.url for item in variant.frames] for variant in payload.variants] == [
        ["media/zoom/a.jpg", "media/zoom/b.jpg"],
        ["media/zoom/a_difference.jpg", "media/zoom/b_difference.jpg"],
    ]


def test_the_field_view_carries_the_corona_images_with_the_view_from_earth() -> None:
    """Each whole-Sun image comes with its disk, the solar tilt and the Carrington longitude facing Earth, and its file is exported."""
    model = pd.DataFrame({FRAME_FILE_FIELD: ["model.json"]}, index=_minutes(30))
    images = pd.DataFrame({FRAME_FILE_FIELD: ["sun.jpg"], DISK_FIELDS[0]: [0.5], DISK_FIELDS[1]: [0.5], DISK_FIELDS[2]: [0.39]}, index=_minutes(5))
    panel = MagneticFieldPanel.model_validate({"type": "fieldlines", "row": 1, "dataset": "pfss", "surface_images": "aia"})

    payload = build_magnetic_field(panel, FakeReader({"pfss": model, "aia": images}, {}), CONTEXT)

    image = payload.surface_images[0]
    assert (image.url, image.disk, round(image.b0), 0 <= image.centre_longitude < 360) == ("media/aia/sun.jpg", (0.5, 0.5, 0.39), -3, True)
    assert ("aia", "sun.jpg") in payload.media_files()


def test_heliosphere_flies_each_cme_from_its_launch_and_links_measured_shocks() -> None:
    """A CME gets distances from 21.5 solar radii on, its expected arrivals and its linked shock; a fast stream is at Earth."""
    cme_id = "2023-12-30T00:00:00-CME-001"
    events = pd.DataFrame(
        {
            "event_type": ["CME", "IPS", "HSS"],
            "event_id": [cme_id, "2024-01-01T06:00:00-IPS-001", "2024-01-01-HSS-001"],
            "linked_ids": ["", cme_id, ""],
            "label": ["1000 km/s", "Earth", None],
            "value": [1000.0, np.nan, np.nan],
            "link": [
                "https://ccmc.gsfc.nasa.gov/DONKI/view/CME/1/-1",
                "https://ccmc.gsfc.nasa.gov/DONKI/view/IPS/2/-1",
                "https://ccmc.gsfc.nasa.gov/DONKI/view/HSS/3/-1",
            ],
            "cme_latitude": [5.0, np.nan, np.nan],
            "cme_longitude": [-10.0, np.nan, np.nan],
            "cme_half_angle": [30.0, np.nan, np.nan],
            "cme_time_21_5": ["2023-12-30T03:00Z", None, None],
            "impacts": ['[{"location": "Earth", "arrival": "2024-01-01T04:00Z", "glancing": false}]', None, None],
        },
        index=pd.DatetimeIndex([NOW - timedelta(days=2, hours=12), NOW - timedelta(hours=6), NOW - timedelta(hours=3)]),
    )
    positions = pd.DataFrame(
        {"body": ["Earth", "Earth"], "kind": ["planet", "planet"], "longitude": [0.0, 0.0], "latitude": [-3.0, -3.0], "distance": [0.983, 0.983]},
        index=pd.DatetimeIndex([NOW - timedelta(days=1), NOW]),
    )
    panel = HeliospherePanel(type="heliosphere", row=1, positions="positions", events="events", history="P29D")

    payload = build_heliosphere(panel, FakeReader({"positions": positions, "events": events}, {}), CONTEXT)

    cme = payload.cmes[0]
    assert (
        cme.launch,
        cme.distances[0],
        cme.ecliptic_half_width is not None,
        cme.wind_speed,
        [arrival.location for arrival in cme.expected],
        cme.observed[0].link,
        payload.bodies[0].name,
        payload.streams[0].location,
    ) == (
        datetime(2023, 12, 30, 3, tzinfo=UTC),
        0.1,
        True,
        400.0,
        ["Earth"],
        "https://ccmc.gsfc.nasa.gov/DONKI/view/IPS/2/-1",
        "Earth",
        "Earth",
    )


def test_magnetic_connection_measures_each_flare_from_the_foot_of_the_field_line_of_each_observer() -> None:
    """At 400 km/s a foot lies about 61 deg west of its observer, so a W60 flare is 1 deg east of Earth's and 91 deg east of a probe 90 deg west."""
    wind = pd.DataFrame({"speed": [400.0] * 3}, index=_minutes(130, 70, 10))
    events = pd.DataFrame(
        {
            "event_type": ["FLR"],
            "event_id": ["2024-01-01T11:00:00-FLR-001"],
            "linked_ids": [""],
            "label": ["M2.1"],
            "link": [""],
            "location": ["N10W60"],
        },
        index=_minutes(60),
    )
    positions = pd.DataFrame(
        {"body": ["Earth", "Probe", "Earth", "Probe"], "longitude": [0.0, 90.0, 0.0, 90.0], "distance": [1.0, 1.0, 1.0, 1.0]},
        index=pd.DatetimeIndex([NOW - timedelta(days=1)] * 2 + [NOW + timedelta(days=1)] * 2),
    )
    flux = pd.DataFrame({"p_ge10mev_flux": [0.4, 0.5]}, index=_minutes(30, 20))
    wind_ref = {"dataset": "wind", "field": "speed"}
    panel = MagneticConnectionPanel(
        type="magnetic_connection",
        row=1,
        events="events",
        positions="positions",
        history="P1D",
        observers=[
            {"body": "Earth", "wind": wind_ref, "particles": [{"dataset": "protons", "field": "p_ge10mev_flux"}]},
            {"body": "Probe", "wind": wind_ref},
        ],
    )

    payload = build_magnetic_connection(panel, FakeReader({"wind": wind, "events": events, "protons": flux, "positions": positions}, {}), CONTEXT)

    angles = payload.flares[0].connection_angles
    assert (angles["Earth"], angles["Probe"], len(payload.observers[0].fluxes[0].values)) == (
        pytest.approx(-1.0, abs=1.0),
        pytest.approx(-91.0, abs=1.0),
        2,
    )


def test_aurora_frames_carry_their_edges_and_the_rule_of_thumb_follows_kp() -> None:
    """Each stored map becomes a frame with its file and edges, and each hourly Kp median gives the edge 66 - 2 Kp."""
    maps = pd.DataFrame(
        {
            "file": ["a.json", "b.json"],
            "edge_north": [62.0, np.nan],
            "edge_south": [63.0, 61.0],
            "subsolar_latitude": [-17.0, -17.0],
            "subsolar_longitude": [0.0, -15.0],
        },
        index=_minutes(60, 30),
    )
    kp = pd.DataFrame({"estimated_kp": [3.0, 4.33]}, index=_minutes(100, 20))
    panel = AuroraPanel(type="aurora", row=1, dataset="maps", kp=[{"dataset": "kp", "field": "estimated_kp"}])

    payload = build_aurora(panel, FakeReader({"maps": maps, "kp": kp}, {}), CONTEXT)

    assert ([frame.url for frame in payload.frames], payload.frames[1].edge_north, payload.kp_edges, payload.media_files()[0]) == (
        ["media/maps/a.json", "media/maps/b.json"],
        None,
        [60.0, 57.34],
        ("maps", "a.json"),
    )


def test_solar_cycle_draws_events_with_energy_and_names_the_strongest_storms() -> None:
    """CMEs without energy are left out, listed arrivals mark CMEs that reached Earth, and the lowest Dst storm is named."""
    days = pd.date_range("2023-01-01", periods=12, freq="D", tz="UTC")
    sunspots = pd.DataFrame({"sunspot_number": np.linspace(50.0, 150.0, 12)}, index=days)
    cmes = pd.DataFrame({"kinetic_energy": [1e30, np.nan, 5e31], "width": [40.0, 30.0, 360.0], "is_halo": [False, False, True]}, index=days[:3])
    dst = pd.Series(-20.0, index=pd.date_range("2023-01-01", periods=24 * 12, freq="h", tz="UTC"))
    dst.iloc[30] = -150.0
    dst.iloc[200] = -250.0
    panel = SolarCyclePanel(
        type="solarcycle",
        row=1,
        sunspots={"dataset": "ssn", "field": "sunspot_number"},
        smoothed={"dataset": "ssn", "field": "sunspot_number"},
        events={"dataset": "cmes", "y": "kinetic_energy", "size": "width", "flag": "is_halo", "flag_label": "Halo", "other_label": "Other"},
        earth={"dataset": "icmes", "field": "cme_times", "label": "Reached Earth"},
        storms={"dataset": "omni", "field": "dst", "threshold": -100},
        labelled=1,
        history="P31Y",
    )

    # The list names the first CME one rounding minute after its catalog time
    icmes = pd.DataFrame({"cme_times": [f"{days[0] + timedelta(minutes=1):%Y-%m-%dT%H:%M}"]}, index=days[2:3])
    reader = FakeReader({"ssn": sunspots, "cmes": cmes, "omni": dst.to_frame("dst"), "icmes": icmes}, {})

    payload = build_solar_cycle(panel, reader, CONTEXT)

    assert (payload.event_values, payload.event_groups, payload.storm_values, payload.labelled) == ([1e30, 5e31], [2, 1], [-150.0, -250.0], [1])


def test_grouped_lines_average_per_bin_and_band_and_shares_count_values_above() -> None:
    """Each band gets its own line of means per bin; a share counts the values at or above the threshold in percent."""
    frame = pd.DataFrame(
        {"bz": [-12.0, -11.0, 2.0, -12.0], "speed": [350.0, 350.0, 350.0, 700.0], "kp": [50.0, 70.0, 10.0, 80.0]}, index=_minutes(40, 30, 20, 10)
    )
    lines = GroupedPanel(
        type="grouped",
        row=1,
        dataset="omni",
        history="P1Y",
        x={"field": "bz", "edges": [-15, -10, 0, 5]},
        bands={"field": "speed", "edges": [0, 400, 3000]},
        band_labels=["slow", "fast"],
        z={"field": "kp", "factor": 0.1},
        y_label="Kp",
        min_count=1,
    )
    share = lines.model_copy(update={"bands": None, "band_labels": [], "statistic": "share_above", "threshold": 5.0})

    line_payload = build_grouped(lines, FakeReader({"omni": frame}, {}), CONTEXT)
    share_payload = build_grouped(share, FakeReader({"omni": frame}, {}), CONTEXT)

    assert ([series.values for series in line_payload.series], share_payload.series[0].values) == (
        [[6.0, None, 1.0], [8.0, None, None]],
        [100.0, None, 0.0],
    )
