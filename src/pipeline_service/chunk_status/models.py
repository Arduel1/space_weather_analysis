from datetime import datetime, timedelta

from pydantic import BaseModel

from space_weather_analysis.util.time_utils import UtcDatetime

# Age after which a fetch of stable data is repeated, as providers may still correct or add data months or years later.
# The fetch times of the chunks differ, so these checks spread over time instead of running as one large run.
RECHECK_AFTER = timedelta(days=90)


class ChunkStatus(BaseModel):
    """What the last successful fetch of one chunk of a dataset covered and whether its data were stable then.

    Chunks are cells of a calendar grid in UTC, so the dataset and the start of its cell identify a chunk.

    Attributes:
        dataset_id: The dataset ID.
        cell_start: The start of the grid cell of the chunk.
        fetched_start: The start of the fetched time range (inclusive), inside the cell.
        fetched_stop: The end of the fetched time range (exclusive), inside the cell.
        fetched_at: When the fetch finished.
        stable: Whether the source considered the fetched data unlikely to change at fetch time.
    """

    dataset_id: str
    cell_start: UtcDatetime
    fetched_start: UtcDatetime
    fetched_stop: UtcDatetime
    fetched_at: UtcDatetime
    stable: bool

    def is_current(self, start: datetime, stop: datetime, now: datetime) -> bool:
        """Tells whether the last fetch makes a new fetch of a time range inside the cell unnecessary.

        Args:
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).
            now: The current time.

        Returns:
            True if the last fetch covered the range, its data were stable and it is younger than RECHECK_AFTER.
        """
        covers_range = self.fetched_start <= start and stop <= self.fetched_stop
        return covers_range and self.stable and now - self.fetched_at < RECHECK_AFTER
