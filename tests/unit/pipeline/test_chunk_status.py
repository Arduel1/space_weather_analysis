from datetime import UTC, datetime, timedelta

import pytest

from pipeline_service.chunk_status.models import RECHECK_AFTER, ChunkStatus

CELL_START = datetime(2024, 1, 1, tzinfo=UTC)
CELL_STOP = CELL_START + timedelta(days=1)
FETCHED_AT = datetime(2024, 3, 1, tzinfo=UTC)


def _status(*, stable: bool = True, fetched_stop: datetime = CELL_STOP) -> ChunkStatus:
    """Builds the status of a fetch of the cell from its start."""
    return ChunkStatus(
        dataset_id="synthetic", cell_start=CELL_START, fetched_start=CELL_START, fetched_stop=fetched_stop, fetched_at=FETCHED_AT, stable=stable
    )


@pytest.mark.parametrize(
    ("status", "now", "expected"),
    [
        pytest.param(_status(), FETCHED_AT + timedelta(days=1), True, id="stable-recent-covering"),
        pytest.param(_status(stable=False), FETCHED_AT + timedelta(days=1), False, id="not-stable"),
        pytest.param(_status(), FETCHED_AT + RECHECK_AFTER, False, id="recheck-due"),
        pytest.param(_status(fetched_stop=CELL_START + timedelta(hours=12)), FETCHED_AT + timedelta(days=1), False, id="range-not-covered"),
    ],
)
def test_last_fetch_is_current_only_if_stable_recent_and_covering(status: ChunkStatus, now: datetime, expected: bool) -> None:
    """A fetch makes a new one unnecessary only if it covered the range, was stable and is younger than the recheck age."""
    assert status.is_current(CELL_START, CELL_STOP, now) is expected
