import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from pipeline_service.feeds.models import Feed, load_feeds
from pipeline_service.feeds.scheduler import due_feeds

PROJECT_FEED_FILE = pathlib.Path(__file__).parents[3] / "config" / "feeds.toml"
NOW = datetime(2024, 3, 1, tzinfo=UTC)


def _write(directory: pathlib.Path, content: str) -> pathlib.Path:
    """Writes a feed file into a directory and returns its path."""
    path = directory / "feeds.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_project_feed_file_is_valid() -> None:
    """The feed file of the project loads with unique, named feeds."""
    assert [feed.name for feed in load_feeds(PROJECT_FEED_FILE)] == ["goes", "swpc_goes", "swpc_kp", "kp", "local"]


@pytest.mark.parametrize(
    ("feed", "expected_range"),
    [
        pytest.param(Feed(name="f", datasets=["d"], lookback=timedelta(days=90)), (NOW - timedelta(days=90), NOW), id="lookback"),
        pytest.param(
            Feed(name="f", datasets=["d"], start=datetime(2020, 1, 1, tzinfo=UTC)), (datetime(2020, 1, 1, tzinfo=UTC), NOW), id="start-until-now"
        ),
        pytest.param(Feed(name="f", datasets=["d"]), (None, None), id="no-range"),
    ],
)
def test_time_range_resolved_at_queue_time(feed: Feed, expected_range: tuple[datetime | None, datetime | None]) -> None:
    """A lookback ends now, a start without end runs until now, and a feed without range leaves it to the pipeline."""
    assert feed.time_range(NOW) == expected_range


def test_feed_file_reads_iso_durations_and_toml_times(tmp_path: pathlib.Path) -> None:
    """Lookbacks and intervals are ISO 8601 durations and fixed bounds TOML date-times."""
    path = _write(
        tmp_path,
        '[[feed]]\nname = "a"\ndatasets = ["x"]\nlookback = "P1M"\ninterval = "PT15M"\n\n'
        '[[feed]]\nname = "b"\ndatasets = ["y"]\nstart = 2020-01-01T00:00:00Z\n',
    )

    feeds = load_feeds(path)

    assert (feeds[0].lookback, feeds[0].interval, feeds[1].start, feeds[1].interval) == (
        timedelta(days=365.2425 / 12),
        timedelta(minutes=15),
        datetime(2020, 1, 1, tzinfo=UTC),
        None,
    )


@pytest.mark.parametrize(
    "content",
    [
        pytest.param('[[feed]]\nname = "a"\ndatasets = ["x"]\nlookback = "P1D"\nstart = 2020-01-01T00:00:00Z\n', id="lookback-and-start"),
        pytest.param('[[feed]]\nname = "a"\ndatasets = ["x"]\nlookback = "90 days"\n', id="no-iso-duration"),
        pytest.param('[[feed]]\nname = "a"\ndatasets = ["x"]\nsource_type = "FTP"\n', id="unknown-source-type"),
        pytest.param('[[feed]]\nname = "a"\ndatasets = ["x"]\n\n[[feed]]\nname = "a"\ndatasets = ["y"]\n', id="repeated-name"),
        pytest.param('[[feed]]\nname = "a"\ndatasets = ["x"]\nintervall = "P1D"\n', id="unknown-key"),
    ],
)
def test_invalid_feed_file_is_rejected(tmp_path: pathlib.Path, content: str) -> None:
    """Contradicting ranges, malformed values, repeated names and unknown keys fail validation."""
    with pytest.raises(ValidationError):
        load_feeds(_write(tmp_path, content))


def test_due_feeds_are_those_whose_interval_has_passed() -> None:
    """A feed is due once its interval has passed or if it was never queued; a feed without interval never is."""
    hourly = Feed(name="hourly", datasets=["a"], interval=timedelta(hours=1))
    quarter_hourly = Feed(name="quarter", datasets=["b"], interval=timedelta(minutes=15))
    never_queued = Feed(name="new", datasets=["c"], interval=timedelta(days=1))
    start_only = Feed(name="start-only", datasets=["d"])
    last_queued = {"hourly": NOW - timedelta(minutes=30), "quarter": NOW - timedelta(minutes=15), "start-only": NOW - timedelta(days=9)}

    due = due_feeds([hourly, quarter_hourly, never_queued, start_only], last_queued, NOW)

    assert [feed.name for feed in due] == ["quarter", "new"]
