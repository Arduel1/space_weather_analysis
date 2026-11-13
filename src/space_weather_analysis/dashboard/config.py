import pathlib
import tomllib
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from space_weather_analysis.dashboard.panel_types import PANEL_TYPES, PanelType
from space_weather_analysis.dashboard.panels.base import PanelConfig, collection_of
from space_weather_analysis.util.time_utils import IsoDuration

# Title of the only page of a dashboard that configures no pages
DEFAULT_PAGE_TITLE = "Dashboard"


class PageConfig(BaseModel):
    """One page of the dashboard, shown as a tab.

    Attributes:
        title: The tab title, which the panels of the page name as their `page`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)


class DashboardConfig(BaseModel):
    """What the dashboard shows, read from the page files of the dashboard directory.

    Attributes:
        page: The pages in tab order; without pages every panel is on one page.
        panel: The panels page by page in the order of the page files, each validated by the model of its type.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    page: list[PageConfig] = Field(default_factory=list)
    panel: list[PanelConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _panels_on_known_pages(self) -> Self:
        """Rejects repeated page titles and, where pages are configured, a panel naming none of them."""
        titles = [page.title for page in self.page]
        if len(set(titles)) != len(titles):
            raise ValueError(f"Page titles must be unique: {titles}")
        if titles:
            unknown = sorted({panel.page for panel in self.panel} - set(titles))
            if unknown:
                raise ValueError(f"Panels name pages that are not configured: {unknown}; configured are {titles}.")
        return self

    @field_validator("panel", mode="before")
    @classmethod
    def _validate_by_type(cls, panels: Any) -> Any:
        """Validates each panel table with the model of the panel type it names."""
        if not isinstance(panels, list):
            return panels
        return [panel_from_table(table, PANEL_TYPES) for table in panels]

    def pages(self) -> list[tuple[str, list[list[PanelConfig]]]]:
        """Groups the panels by page in the order of the pages, and within a page into rows sorted by row number.

        Returns:
            The title and rows of each page; one page titled DEFAULT_PAGE_TITLE if no pages are configured.
        """
        if not self.page:
            return [(DEFAULT_PAGE_TITLE, _rows(self.panel))]
        return [(page.title, _rows([panel for panel in self.panel if panel.page == page.title])) for page in self.page]

    def datasets(self) -> set[str]:
        """Lists every dataset the panels read."""
        return {dataset for panel in self.panel for dataset in panel.datasets()}

    def collections(self) -> set[str]:
        """Lists the collections of every dataset the panels read."""
        return {collection_of(dataset) for dataset in self.datasets()}


def _rows(panels: list[PanelConfig]) -> list[list[PanelConfig]]:
    """Groups panels into rows, sorted by row number, each in the given order."""
    by_row: dict[int, list[PanelConfig]] = {}
    for panel in panels:
        by_row.setdefault(panel.row, []).append(panel)
    return [by_row[row] for row in sorted(by_row)]


def panel_from_table(table: Any, panel_types: Mapping[str, PanelType[Any]]) -> Any:
    """Validates one panel table with the model of its type.

    Args:
        table: The panel table of a page file.
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


# The file of the dashboard directory naming the page files in tab order
PAGE_ORDER_FILE = "pages.toml"


class _PageOrder(BaseModel):
    """The page order file: the names of the page files of the dashboard directory in tab order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pages: list[str] = Field(min_length=1)


class _PageFile(BaseModel):
    """One page file: the title of the page and its panel tables, which name no page as the file gives it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1)
    panel: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("panel")
    @classmethod
    def _no_page_key(cls, panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rejects a panel table naming a page, which could contradict the file it is in."""
        if any("page" in table for table in panels):
            raise ValueError("A panel of a page file names no `page`; the page file gives it.")
        return panels


def _read_toml(path: pathlib.Path) -> dict[str, Any]:
    """Reads one TOML file."""
    with path.open("rb") as file:
        return tomllib.load(file)


class _FedDashboard(BaseModel):
    """A dashboard together with the datasets the feeds load, to check that the feeds keep every shown dataset current."""

    fed: set[str]
    dashboard: DashboardConfig

    @model_validator(mode="after")
    def _shown_datasets_are_fed(self) -> Self:
        """Rejects a dashboard that shows a dataset no feed keeps loaded."""
        unfed = sorted(self.dashboard.datasets() - self.fed)
        if unfed:
            raise ValueError(f"The dashboard shows datasets that no feed loads: {unfed}")
        return self


def load_dashboard_config(dashboard_dir: pathlib.Path, feeds_file: pathlib.Path) -> DashboardConfig:
    """Reads the page files of the dashboard directory in tab order and checks that every shown dataset belongs to a feed.

    Args:
        dashboard_dir: The directory holding the page order file PAGE_ORDER_FILE and one page file per page.
        feeds_file: The path of the feed file.

    Returns:
        The dashboard configuration with the pages in the order of the page order file and the panels of each page in
        the order of its file.

    Raises:
        OSError: If a file cannot be read.
        tomllib.TOMLDecodeError: If a file is no valid TOML.
        pydantic.ValidationError: If a file or panel is invalid, a panel names an unknown type or shows a dataset no feed
            loads.
    """
    order = _PageOrder.model_validate(_read_toml(dashboard_dir / PAGE_ORDER_FILE))
    pages = [_PageFile.model_validate(_read_toml(dashboard_dir / f"{name}.toml")) for name in order.pages]
    # Each panel takes the title of its page file as its page, so the pages concatenate into the panel list of the dashboard
    panels = [{**table, "page": page.title} for page in pages for table in page.panel]
    fed = {dataset for feed in _read_feeds(feeds_file) for dataset in feed.datasets}
    dashboard = {"page": [{"title": page.title} for page in pages], "panel": panels}
    return _FedDashboard.model_validate({"fed": fed, "dashboard": dashboard}).dashboard


class _FeedInterval(BaseModel):
    """The parts of one feed table that the dashboard needs to rate how current its data are."""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    datasets: list[str] = Field(default_factory=list)
    interval: IsoDuration | None = None


def _read_feeds(path: pathlib.Path) -> list[_FeedInterval]:
    """Reads name, datasets and interval of every feed of the feed file."""
    return [_FeedInterval.model_validate(table) for table in _read_toml(path).get("feed", [])]


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
    return {feed.name: feed.interval for feed in _read_feeds(path)}


def load_feed_datasets(path: pathlib.Path) -> dict[str, tuple[str, ...]]:
    """Reads the datasets of each feed of the feed file.

    Args:
        path: The path of the feed file.

    Returns:
        The dataset IDs by feed name, in the order of the file.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is no valid TOML.
        pydantic.ValidationError: If a feed gives an interval that is no ISO 8601 duration.
    """
    return {feed.name: tuple(feed.datasets) for feed in _read_feeds(path)}


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
    feeds = _read_feeds(path)
    intervals: dict[str, timedelta | None] = {}
    for feed in feeds:
        for dataset in feed.datasets:
            current = intervals.get(dataset)
            if feed.interval is not None and (current is None or feed.interval < current):
                intervals[dataset] = feed.interval
            else:
                intervals.setdefault(dataset, current)
    return intervals


# The failures of load_dashboard_config and of the feed readers, for callers that show them instead of raising
CONFIG_ERRORS = (OSError, tomllib.TOMLDecodeError, ValidationError)
