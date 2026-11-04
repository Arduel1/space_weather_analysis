import pathlib
import tomllib
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import IsoDuration


class DashboardSettings(BaseSettings):
    """Settings of the dashboard, read from environment variables `FRONTEND_{field name}` in upper case.

    Attributes:
        feeds_file: The feed file, whose `dashboard` table configures what the dashboard shows.
    """

    model_config = SettingsConfigDict(env_prefix="FRONTEND_", frozen=True)

    feeds_file: pathlib.Path = pathlib.Path("config/feeds.toml")


# The model of every table is closed, so a mistyped key fails the validation instead of being ignored
_CLOSED = ConfigDict(extra="forbid", frozen=True)


class DatasetRef(BaseModel):
    """A dataset that the dashboard reads.

    Attributes:
        dataset: The dataset ID, which must belong to a feed.
    """

    model_config = _CLOSED

    dataset: str = Field(min_length=1)

    @property
    def collection(self) -> str:
        """The collection holding the documents of the dataset, named like the pipelines name it."""
        return sanitize_collection_name(self.dataset)


class FieldRef(DatasetRef):
    """A data field of a dataset that the dashboard reads.

    Attributes:
        field: The data field.
    """

    field: str = Field(min_length=1)


class KpTile(FieldRef):
    """The Kp tile: the official Kp and, optionally, an estimate that is published sooner.

    Attributes:
        estimate: The estimated Kp shown as the current value; None shows the official Kp as current value.
    """

    estimate: FieldRef | None = None


class Tiles(BaseModel):
    """The key figure tiles of the dashboard; a tile that is not configured is not shown.

    Attributes:
        kp: The Kp index with its NOAA geomagnetic storm level.
        xray: The 0.1-0.8 nm X-ray flux as flare class with its NOAA radio blackout level.
        protons: The >=10 MeV integral proton flux with its NOAA solar radiation storm level.
        sunspots: The daily sunspot number.
    """

    model_config = _CLOSED

    kp: KpTile | None = None
    xray: FieldRef | None = None
    protons: FieldRef | None = None
    sunspots: FieldRef | None = None


class ChartWindow(BaseModel):
    """The time window of a chart: what it shows on load and how far back its data reach when panned.

    Attributes:
        view: The span shown, ending now and moving with the time on every update.
        history: The span of loaded data, ending with the newest stored document, reachable by panning; at least the view.
    """

    model_config = _CLOSED

    view: IsoDuration
    history: IsoDuration

    @model_validator(mode="after")
    def _history_covers_view(self) -> Self:
        """Rejects a history shorter than the view, which would leave the start of the view empty."""
        if self.history < self.view:
            raise ValueError("The history of a chart must be at least as long as its view.")
        return self


class KpChart(FieldRef, ChartWindow):
    """The bar chart of the official Kp, with the estimated Kp as line.

    Attributes:
        estimate: The estimated Kp drawn as line; None draws none.
    """

    estimate: FieldRef | None = None


class XrayChart(DatasetRef, ChartWindow):
    """The log chart of both GOES X-ray channels with flare classes and radio blackout levels.

    Attributes:
        long_field: The field of the 0.1-0.8 nm channel in W/m^2.
        short_field: The field of the 0.05-0.4 nm channel in W/m^2.
    """

    long_field: str = Field(min_length=1)
    short_field: str = Field(min_length=1)


class FieldChart(FieldRef, ChartWindow):
    """A chart of one field."""


class SunspotChart(ChartWindow):
    """The chart of the monthly mean and the smoothed sunspot number.

    Attributes:
        monthly: The monthly mean sunspot number.
        smoothed: The 13-month smoothed sunspot number; None draws no smoothed line.
    """

    monthly: FieldRef
    smoothed: FieldRef | None = None


class CmeTable(DatasetRef):
    """The table of the most recent CMEs (coronal mass ejections).

    Attributes:
        span: The time span searched for events, ending with the newest stored event.
        count: The number of events shown.
    """

    span: IsoDuration
    count: int = Field(default=20, gt=0)


class Charts(BaseModel):
    """The charts of the dashboard; a chart that is not configured is not shown.

    Attributes:
        kp: The Kp chart.
        xray: The X-ray chart.
        protons: The >=10 MeV proton chart.
        sunspots: The sunspot chart.
        cmes: The table of recent CMEs.
    """

    model_config = _CLOSED

    kp: KpChart | None = None
    xray: XrayChart | None = None
    protons: FieldChart | None = None
    sunspots: SunspotChart | None = None
    cmes: CmeTable | None = None


class DashboardConfig(BaseModel):
    """What the dashboard shows, the `dashboard` table of the feed file.

    Attributes:
        tiles: The key figure tiles.
        charts: The charts.
    """

    model_config = _CLOSED

    tiles: Tiles = Field(default_factory=Tiles)
    charts: Charts = Field(default_factory=Charts)

    def datasets(self) -> set[str]:
        """Lists every dataset the dashboard reads."""
        found: set[str] = set()
        _collect_datasets(self.model_dump(), found)
        return found

    def collections(self) -> set[str]:
        """Lists the collections of every dataset the dashboard reads."""
        return {sanitize_collection_name(dataset) for dataset in self.datasets()}


def _collect_datasets(table: Any, found: set[str]) -> None:
    """Adds the value of every `dataset` key of a nested table and its subtables to a set."""
    if not isinstance(table, dict):
        return
    for key, value in table.items():
        if key == "dataset" and isinstance(value, str):
            found.add(value)
        else:
            _collect_datasets(value, found)


class _FeedFileView(BaseModel):
    """The parts of the feed file the dashboard needs: the datasets of the feeds and the dashboard table."""

    feed: list[dict[str, Any]] = Field(default_factory=list)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)

    @model_validator(mode="after")
    def _shown_datasets_are_fed(self) -> Self:
        """Rejects a dashboard that shows a dataset no feed keeps loaded."""
        fed = {dataset for feed in self.feed for dataset in feed.get("datasets", [])}
        unfed = sorted(self.dashboard.datasets() - fed)
        if unfed:
            raise ValueError(f"The dashboard shows datasets that no feed loads: {unfed}")
        return self


def load_dashboard_config(path: pathlib.Path) -> DashboardConfig:
    """Reads the dashboard table of the feed file and checks that every shown dataset belongs to a feed.

    Args:
        path: The path of the feed file.

    Returns:
        The dashboard configuration.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is no valid TOML.
        pydantic.ValidationError: If the dashboard table is invalid or shows a dataset no feed loads.
    """
    with path.open("rb") as file:
        return _FeedFileView.model_validate(tomllib.load(file)).dashboard


# The failures of load_dashboard_config, for callers that show them instead of raising
CONFIG_ERRORS = (OSError, tomllib.TOMLDecodeError, ValidationError)
