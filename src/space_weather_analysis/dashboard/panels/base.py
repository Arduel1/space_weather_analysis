from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from space_weather_analysis.dashboard.scales import FORMATTERS, SCALES, Level, Scale, threshold_scale
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import IsoDuration

# The model of every table is closed, so a mistyped key fails the validation instead of being ignored
CLOSED = ConfigDict(extra="forbid", frozen=True)


def collection_of(dataset: str) -> str:
    """Names the collection holding the documents of a dataset, like the pipelines name it."""
    return sanitize_collection_name(dataset)


class PanelConfig(BaseModel):
    """The settings every panel shares; each panel type adds its own.

    Attributes:
        type: The panel type, a key of the panel type registry.
        title: The heading of the panel.
        row: The dashboard row of the panel, from 1 at the top; panels of a row share it in the order of the file.
        width: The width of the panel relative to the other panels of its row.
    """

    model_config = CLOSED

    type: str
    title: str = ""
    row: int = Field(ge=1)
    width: float = Field(default=1.0, gt=0)

    def datasets(self) -> set[str]:
        """Lists the dataset of every `dataset` key of the panel settings."""
        found: set[str] = set()
        _collect_datasets(self.model_dump(), found)
        return found


def _collect_datasets(table: Any, found: set[str]) -> None:
    """Adds the value of every `dataset` key of a nested table, its subtables and lists to a set."""
    if isinstance(table, dict):
        for key, value in table.items():
            if key == "dataset" and isinstance(value, str):
                found.add(value)
            else:
                _collect_datasets(value, found)
    elif isinstance(table, list):
        for item in table:
            _collect_datasets(item, found)


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
    """

    model_config = CLOSED

    view: IsoDuration
    history: IsoDuration

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
    """

    now: datetime
    feed_intervals: Mapping[str, timedelta | None]
    feed_schedule: Mapping[str, timedelta | None] = field(default_factory=dict)


# Payloads are frozen, as they are only built and then serialized
PAYLOAD = ConfigDict(frozen=True)


class PanelPayload(BaseModel):
    """The JSON document of one panel, as a web frontend draws it; each panel type adds what it shows.

    Attributes:
        type: The panel type, a key of the panel type registry.
    """

    model_config = PAYLOAD

    type: str
