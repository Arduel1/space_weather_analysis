import pathlib
import tomllib
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from space_weather_analysis.util.time_utils import IsoDuration
from streamlit_app.dashboard.panel_types import PANEL_TYPES, PanelType
from streamlit_app.dashboard.panels.base import PanelConfig, collection_of


class DashboardSettings(BaseSettings):
    """Settings of the dashboard, read from environment variables `FRONTEND_{field name}` in upper case.

    Attributes:
        feeds_file: The feed file, whose `dashboard` table configures what the dashboard shows.
    """

    model_config = SettingsConfigDict(env_prefix="FRONTEND_", frozen=True)

    feeds_file: pathlib.Path = pathlib.Path("config/feeds.toml")


class DashboardConfig(BaseModel):
    """What the dashboard shows, the `dashboard` table of the feed file.

    Attributes:
        panel: The panels in the order of the file, each validated by the model of its type.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    panel: list[PanelConfig] = Field(default_factory=list)

    @field_validator("panel", mode="before")
    @classmethod
    def _validate_by_type(cls, panels: Any) -> Any:
        """Validates each panel table with the model of the panel type it names."""
        if not isinstance(panels, list):
            return panels
        return [panel_from_table(table, PANEL_TYPES) for table in panels]

    def rows(self) -> list[list[PanelConfig]]:
        """Groups the panels into rows, sorted by row number, each in the order of the file."""
        by_row: dict[int, list[PanelConfig]] = {}
        for panel in self.panel:
            by_row.setdefault(panel.row, []).append(panel)
        return [by_row[row] for row in sorted(by_row)]

    def datasets(self) -> set[str]:
        """Lists every dataset the panels read."""
        return {dataset for panel in self.panel for dataset in panel.datasets()}

    def collections(self) -> set[str]:
        """Lists the collections of every dataset the panels read."""
        return {collection_of(dataset) for dataset in self.datasets()}


def panel_from_table(table: Any, panel_types: Mapping[str, PanelType[Any]]) -> Any:
    """Validates one panel table with the model of its type.

    Args:
        table: The panel table of the file.
        panel_types: The known panel types by name.

    Returns:
        The validated panel, or the table unchanged if it is no table, so pydantic reports it.

    Raises:
        ValueError: If the table names no known panel type.
    """
    if not isinstance(table, dict) or isinstance(table, PanelConfig):
        return table
    panel_type = panel_types.get(str(table.get("type")))
    if panel_type is None:
        raise ValueError(f"Unknown panel type '{table.get('type')}'; known are {sorted(panel_types)}.")
    return panel_type.model.model_validate(table)


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
        pydantic.ValidationError: If a panel is invalid, names an unknown type or shows a dataset no feed loads.
    """
    with path.open("rb") as file:
        return _FeedFileView.model_validate(tomllib.load(file)).dashboard


class _FeedInterval(BaseModel):
    """The parts of one feed table that the dashboard needs to rate how current its data are."""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    datasets: list[str] = Field(default_factory=list)
    interval: IsoDuration | None = None


def _read_feed_intervals(path: pathlib.Path) -> list[_FeedInterval]:
    """Reads name, datasets and interval of every feed of the feed file."""
    with path.open("rb") as file:
        return [_FeedInterval.model_validate(table) for table in tomllib.load(file).get("feed", [])]


def load_feed_schedule(path: pathlib.Path) -> dict[str, timedelta | None]:
    """Reads the interval of each feed of the feed file.

    Args:
        path: The path of the feed file.

    Returns:
        The interval by feed name; None for feeds loaded only on start.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is no valid TOML.
        pydantic.ValidationError: If a feed gives an interval that is no ISO 8601 duration.
    """
    return {feed.name: feed.interval for feed in _read_feed_intervals(path)}


def load_feed_intervals(path: pathlib.Path) -> dict[str, timedelta | None]:
    """Reads the interval of the feed loading each dataset from the feed file.

    Args:
        path: The path of the feed file.

    Returns:
        The interval by dataset ID, the shortest one for a dataset of several feeds; None for feeds loaded only on start.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is no valid TOML.
        pydantic.ValidationError: If a feed gives an interval that is no ISO 8601 duration.
    """
    feeds = _read_feed_intervals(path)
    intervals: dict[str, timedelta | None] = {}
    for feed in feeds:
        for dataset in feed.datasets:
            current = intervals.get(dataset)
            if feed.interval is not None and (current is None or feed.interval < current):
                intervals[dataset] = feed.interval
            else:
                intervals.setdefault(dataset, current)
    return intervals


# The failures of load_dashboard_config, load_feed_intervals and load_feed_schedule, for callers that show them instead of raising
CONFIG_ERRORS = (OSError, tomllib.TOMLDecodeError, ValidationError)
