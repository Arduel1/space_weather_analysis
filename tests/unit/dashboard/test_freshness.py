from datetime import UTC, datetime, timedelta

import pytest

from space_weather_analysis.dashboard.freshness import (
    DELAYED_FACTOR,
    PUBLICATION_DELAY,
    Freshness,
    age_text,
    expected_age,
    freshness,
)
from space_weather_analysis.dashboard.labels import Metadata

NOW = datetime(2024, 1, 1, 12, tzinfo=UTC)
EXPECTED = timedelta(minutes=20)
TINY = timedelta(seconds=1)


@pytest.mark.parametrize(
    ("interval", "metadata", "expected"),
    [
        pytest.param(None, {"time": {"cadence": "PT3H"}}, None, id="no-interval"),
        pytest.param(timedelta(minutes=1), {}, timedelta(minutes=1) + PUBLICATION_DELAY, id="unknown-cadence"),
        pytest.param(
            timedelta(minutes=1),
            {"time": {"cadence": "PT3H", "timestamp_location": "center"}},
            timedelta(minutes=1, hours=3) + PUBLICATION_DELAY,
            id="interval-and-cadence",
        ),
        pytest.param(
            timedelta(minutes=1),
            {"time": {"cadence": "PT3H", "timestamp_location": "begin"}},
            timedelta(minutes=1, hours=6) + PUBLICATION_DELAY,
            id="begin-stamp-adds-a-cadence",
        ),
    ],
)
def test_expected_age_adds_interval_cadence_and_publication_delay(interval: timedelta | None, metadata: Metadata, expected: timedelta | None) -> None:
    """The expected age is the feed interval plus one cadence, one more for begin time stamps, plus the publication delay."""
    assert expected_age(interval, metadata) == expected


@pytest.mark.parametrize(
    ("age", "expected", "state"),
    [
        pytest.param(EXPECTED, EXPECTED, Freshness.LIVE, id="exactly-expected-is-live"),
        pytest.param(EXPECTED + TINY, EXPECTED, Freshness.DELAYED, id="just-above-expected-is-delayed"),
        pytest.param(EXPECTED * DELAYED_FACTOR, EXPECTED, Freshness.DELAYED, id="exactly-delayed-factor-is-delayed"),
        pytest.param(EXPECTED * DELAYED_FACTOR + TINY, EXPECTED, Freshness.STALE, id="above-delayed-factor-is-stale"),
        pytest.param(timedelta(days=400), None, Freshness.STATIC, id="no-expected-age-is-static"),
    ],
)
def test_freshness_rates_the_age_against_the_expected_age(age: timedelta, expected: timedelta | None, state: Freshness) -> None:
    """Data up to the expected age are live, up to DELAYED_FACTOR times it delayed, older stale, and without expected age static."""
    assert freshness(NOW - age, NOW, expected) == state


@pytest.mark.parametrize(
    ("age", "text"),
    [
        pytest.param(timedelta(seconds=59), "59 s", id="seconds"),
        pytest.param(timedelta(minutes=59, seconds=59), "59 min", id="minutes"),
        pytest.param(timedelta(hours=47, minutes=59), "47 h", id="hours-below-two-days"),
        pytest.param(timedelta(days=2), "2 d", id="days"),
    ],
)
def test_age_text_uses_the_largest_whole_unit(age: timedelta, text: str) -> None:
    """An age is written in seconds, minutes, hours below two days, or days."""
    assert age_text(age) == text
