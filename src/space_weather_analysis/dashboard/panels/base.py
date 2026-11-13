import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from space_weather_analysis.dashboard.about import DatasetAbout
from space_weather_analysis.dashboard.scales import FORMATTERS, SCALES, Level, Scale, threshold_scale
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import IsoDuration

# The model of every table is closed, so a mistyped key fails the validation instead of being ignored
CLOSED = ConfigDict(extra="forbid", frozen=True)


def collection_of(dataset: str) -> str:
    """Names the collection holding the documents of a dataset, like the pipelines name it."""
    return sanitize_collection_name(dataset)


def text_or_empty(value: object) -> str:
    """Returns a stored text value; an empty text for missing values and non-text values."""
    return value if isinstance(value, str) else ""


def finite_number(value: object) -> float | None:
    """Returns a stored finite number as float; None for missing values, text, booleans, NaN and infinity."""
    # bool is a subclass of int but no measurement
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


class PanelConfig(BaseModel):
    """The settings every panel shares; each panel type adds its own.

    Attributes:
        type: The panel type, a key of the panel type registry.
        page: The title of the page showing the panel; empty where the dashboard configures no pages.
        title: The heading of the panel.
        row: The dashboard row of the panel, from 1 at the top; panels of a row share it in the order of the file.
        width: The width of the panel relative to the other panels of its row.
        below: Draws the panel below the previous panel of its row, in the same column, whose width it shares.
    """

    model_config = CLOSED

    type: str
    page: str = ""
    title: str = ""
    row: int = Field(ge=1)
    width: float = Field(default=1.0, gt=0)
    below: bool = False

    def datasets(self) -> set[str]:
        """Lists the dataset of every `dataset` key of the panel settings."""
        found: set[str] = set()
        _collect_datasets(self.model_dump(), found)
        return found

    def shown_fields(self) -> dict[str, list[str]]:
        """Lists the fields the panel shows by dataset, from every table of its settings naming a `dataset` and a `field`.

        Returns:
            The fields of each dataset in the order of the settings, each once.
        """
        found: dict[str, list[str]] = {}
        _collect_fields(self.model_dump(), found)
        return found


def _collect_datasets(table: Any, found: set[str]) -> None:
    """Adds the value of every `dataset` key of a nested table, its subtables and lists to a set."""
    if isinstance(table, dict):
        for key, value in table.items():
            # A derived series names no dataset of its own, only the datasets of its inputs
            if key == "dataset" and isinstance(value, str):
                if value:
                    found.add(value)
            else:
                _collect_datasets(value, found)
    elif isinstance(table, list):
        for item in table:
            _collect_datasets(item, found)


def _collect_fields(table: Any, found: dict[str, list[str]]) -> None:
    """Adds the field of every nested table naming a `dataset` and a `field` to the fields of that dataset."""
    if isinstance(table, dict):
        dataset, field = table.get("dataset"), table.get("field")
        if isinstance(dataset, str) and isinstance(field, str) and dataset and field and field not in found.setdefault(dataset, []):
            found[dataset].append(field)
        for value in table.values():
            _collect_fields(value, found)
    elif isinstance(table, list):
        for item in table:
            _collect_fields(item, found)


class ThresholdConfig(BaseModel):
    """One level of a scale given in the configuration.

    Attributes:
        value: The lowest value of the level.
        label: The name shown for the level.
        color: The color of the level, any CSS color.
    """

    model_config = CLOSED

    value: float
    label: str
    color: str = "#E67E22"


class ScaleChoice(BaseModel):
    """The scale of a panel: a named scale or own thresholds, at most one of them.

    Attributes:
        scale: The name of a registered scale.
        thresholds: Own levels, sorted by ascending value.
    """

    model_config = CLOSED

    scale: str | None = None
    thresholds: list[ThresholdConfig] | None = None

    @model_validator(mode="after")
    def _known_single_scale(self) -> Self:
        """Rejects an unknown scale name, unsorted thresholds and a panel giving both."""
        if self.scale is not None and self.thresholds is not None:
            raise ValueError("Give either a scale or thresholds, not both.")
        if self.scale is not None and self.scale not in SCALES:
            raise ValueError(f"Unknown scale '{self.scale}'; known are {sorted(SCALES)}.")
        values = [threshold.value for threshold in self.thresholds or []]
        if values != sorted(values):
            raise ValueError("Thresholds must be sorted by ascending value.")
        return self

    def resolved_scale(self) -> Scale | None:
        """Returns the named scale or the scale of the own thresholds; None without either."""
        if self.scale is not None:
            return SCALES[self.scale]
        if self.thresholds:
            return threshold_scale([Level(threshold.value, threshold.label, threshold.color) for threshold in self.thresholds])
        return None


class ValueFormat(BaseModel):
    """How a panel writes values.

    Attributes:
        format: A key of the named formatters or a Python format string with one field like `{:.2f}`.
        unit: Text written after the value; empty for none.
    """

    model_config = CLOSED

    format: str = "{:.3g}"
    unit: str = ""

    @model_validator(mode="after")
    def _usable_format(self) -> Self:
        """Rejects a format that is neither a named formatter nor a format string for one number."""
        if self.format not in FORMATTERS:
            try:
                self.format.format(1.0)
            except (IndexError, KeyError, ValueError) as error:
                raise ValueError(f"Format '{self.format}' is no named formatter and no format string for one number: {error}") from error
        return self


class ValidityChoice(BaseModel):
    """Which values of a field a panel hides as no measurement.

    Attributes:
        hide_invalid: Whether fill values and values outside the valid range are hidden at first.
        valid_min: The smallest valid value, taking precedence over the metadata; None takes the metadata.
        valid_max: The largest valid value, taking precedence over the metadata; None takes the metadata.
    """

    model_config = CLOSED

    hide_invalid: bool = True
    valid_min: float | None = None
    valid_max: float | None = None


class TimeWindow(BaseModel):
    """The time window of a panel over time.

    Attributes:
        view: The span shown, ending now and moving with the time on every update.
        history: The span of loaded data, ending with the newest stored document, reachable by panning; at least the view.
        ahead: The span shown after now besides the view, for model values of the coming days; none by default.
    """

    model_config = CLOSED

    view: IsoDuration
    history: IsoDuration
    ahead: IsoDuration = timedelta(0)

    @model_validator(mode="after")
    def _history_covers_view(self) -> Self:
        """Rejects a history shorter than the view, which would leave the start of the view empty."""
        if self.history < self.view:
            raise ValueError("The history of a panel must be at least as long as its view.")
        return self


@dataclass(frozen=True)
class PanelContext:
    """What every panel gets for one drawing besides its settings.

    Attributes:
        now: The time of the drawing.
        feed_intervals: The interval of the feed loading each dataset, by dataset ID; None for a feed loaded only on start.
        feed_schedule: The interval of each feed, by feed name; None for a feed loaded only on start.
        feed_datasets: The datasets of each feed, by feed name.
    """

    now: datetime
    feed_intervals: Mapping[str, timedelta | None]
    feed_schedule: Mapping[str, timedelta | None] = field(default_factory=dict)
    feed_datasets: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


# Payloads are frozen, as they are only built and then serialized
PAYLOAD = ConfigDict(frozen=True)


class PanelPayload(BaseModel):
    """The JSON document of one panel, as a web frontend draws it; each panel type adds what it shows.

    Attributes:
        type: The panel type, a key of the panel type registry.
        about: What the metadata records say about the datasets and fields of the panel, for its info view.
    """

    model_config = PAYLOAD

    type: str
    about: list[DatasetAbout] = Field(default_factory=list)

    def media_files(self) -> list[tuple[str, str]]:
        """Lists the media files the payload refers to as pairs of dataset ID and file name; none for most panel types."""
        return []
