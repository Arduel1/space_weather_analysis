import pathlib
import tomllib
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.util.time_utils import IsoDuration, UtcDatetime


class Feed(BaseModel):
    """A group of datasets the pipeline service keeps loaded, queued as one job on every start and then every interval.

    The time range is either a lookback up to now or fixed bounds; without both, the pipelines choose the range.

    Attributes:
        name: The unique name of the feed, stored with its jobs.
        datasets: The IDs of the datasets to load.
        source_type: The source type of the pipeline; None lets the job runner resolve it from the master catalog.
        lookback: The length of the time range that ends now.
        start: The fixed start of the time range.
        end: The fixed end of the time range; None ends a range with lookback or start now.
        interval: The time between two jobs of the feed; None queues a job only on start.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    datasets: list[str] = Field(min_length=1)
    source_type: SourceType | None = None
    lookback: IsoDuration | None = None
    start: UtcDatetime | None = None
    end: UtcDatetime | None = None
    interval: IsoDuration | None = None

    @model_validator(mode="after")
    def _one_kind_of_range(self) -> Self:
        """Rejects a feed that gives both a lookback and fixed bounds."""
        if self.lookback is not None and (self.start is not None or self.end is not None):
            raise ValueError(f"Feed '{self.name}' gives a lookback and fixed bounds; give only one of them.")
        return self

    def time_range(self, now: datetime) -> tuple[datetime | None, datetime | None]:
        """Resolves the time range of the feed for a job queued now.

        Args:
            now: The current time.

        Returns:
            The start and end; both None if the feed gives no range.
        """
        if self.lookback is not None:
            return now - self.lookback, now
        if self.start is not None:
            return self.start, self.end or now
        return None, self.end


class FeedFile(BaseModel):
    """The content of the feed configuration file, a TOML array of tables named `feed` and the table `dashboard`.

    Attributes:
        feed: The feeds in the order of the file.
        dashboard: What the dashboard shows of the feeds; read and validated by the frontend.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    feed: list[Feed] = Field(default_factory=list)
    dashboard: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        """Rejects a file in which two feeds share a name, as the name identifies the jobs of a feed."""
        names = [feed.name for feed in self.feed]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"Feed names must be unique, repeated: {duplicates}")
        return self


def load_feeds(path: pathlib.Path) -> list[Feed]:
    """Reads the feeds of a TOML configuration file.

    Args:
        path: The path of the file.

    Returns:
        The feeds in the order of the file.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is no valid TOML.
        pydantic.ValidationError: If the content does not describe valid feeds.
    """
    with path.open("rb") as file:
        return FeedFile.model_validate(tomllib.load(file)).feed
