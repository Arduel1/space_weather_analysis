import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from pydantic import Field

from space_weather_analysis.dashboard.freshness import DatasetFreshness, dataset_freshness
from space_weather_analysis.dashboard.labels import Metadata, dataset_title, field_description, field_label, field_unit, sources_line
from space_weather_analysis.dashboard.panels.base import PanelConfig, PanelContext, PanelPayload, ScaleChoice, ValidityChoice, ValueFormat
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.dashboard.scales import FORMATTERS, Level, format_value
from space_weather_analysis.dashboard.validity import describe_validity, valid_range


class ValuePanel(PanelConfig, ScaleChoice, ValueFormat, ValidityChoice):
    """A tile with the newest value of one field and, with a scale, its level; a hidden invalid value shows as "invalid".

    Attributes:
        dataset: The dataset ID, which must belong to a feed.
        field: The data field.
    """

    dataset: str = Field(min_length=1)
    field: str = Field(min_length=1)


@dataclass(frozen=True)
class ValueView:
    """What a value tile shows.

    Attributes:
        title: The heading: the configured title, else the label of the field.
        text: The formatted value with unit, "n/a" without a value.
        time: The UTC time stamp of the value; None without a value.
        level: The level of the value; None without scale, value or level.
        help_lines: The description of the field, the title of its dataset and its valid values, each where known.
        source: The source line of the dataset.
    """

    title: str
    text: str
    time: datetime | None
    level: Level | None
    help_lines: tuple[str, ...] = ()
    source: str = ""


def value_view(panel: ValuePanel, latest: tuple[datetime, dict[str, Any]] | None, metadata: Metadata) -> ValueView:
    """Builds what a value tile shows from the newest document of its dataset and the metadata record of the dataset.

    Configured title and unit take precedence over the metadata, which takes precedence over the field name; a value
    written by a named formatter gets no unit.

    Args:
        panel: The panel settings.
        latest: The time stamp and data fields of the newest document; None if there is none.
        metadata: The metadata record of the dataset; empty if it has none.

    Returns:
        The view; a missing, non-numeric or non-finite value shows "n/a" without level.
    """
    title = panel.title or field_label(metadata, panel.field)
    empty = ValueView(title=title, text="n/a", time=None, level=None, help_lines=tuple(_help_lines(panel, metadata)), source=sources_line([metadata]))
    if latest is None:
        return empty
    time, fields = latest
    value = fields.get(panel.field)
    # bool is a subclass of int but no measurement
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return replace(empty, time=time)
    if panel.hide_invalid and not valid_range(metadata, panel.field, panel.valid_min, panel.valid_max).is_valid(float(value)):
        return replace(empty, text="invalid", time=time)
    # A named formatter writes a complete text such as a flare class, which takes no unit
    unit = "" if panel.format in FORMATTERS else field_unit(metadata, panel.field, panel.unit)
    scale = panel.resolved_scale()
    level = scale.level_of(float(value)) if scale is not None else None
    return replace(empty, text=f"{format_value(float(value), panel.format)} {unit}".rstrip(), time=time, level=level)


def _help_lines(panel: ValuePanel, metadata: Metadata) -> list[str]:
    """Lists the lines of the tile help: field description, dataset title and, where known, the valid values."""
    lines = []
    description = field_description(metadata, panel.field)
    if description:
        lines.append(description)
    title = dataset_title(metadata)
    if title:
        lines.append(f"Dataset: {title}")
    if valid_range(metadata, panel.field, panel.valid_min, panel.valid_max).is_known:
        lines.append(f"Valid values: {describe_validity(metadata, panel.field, panel.valid_min, panel.valid_max)}")
    return lines


class ValuePayload(PanelPayload):
    """The JSON document of a value tile.

    Attributes:
        view: What the tile shows.
        freshness: How current the dataset is; empty without a stored value.
    """

    view: ValueView
    freshness: list[DatasetFreshness]


def build_value(panel: ValuePanel, reader: DashboardReader, context: PanelContext) -> ValuePayload:
    """Builds the JSON document of a value tile from the newest document and the metadata of its dataset.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export, with the feed interval of each dataset.

    Returns:
        The payload.
    """
    metadata = reader.metadata(panel.dataset)
    view = value_view(panel, reader.latest(panel.dataset, (panel.field,)), metadata)
    freshness = [] if view.time is None else [dataset_freshness(panel.dataset, view.time, metadata, context.feed_intervals.get(panel.dataset))]
    return ValuePayload(type=panel.type, view=view, freshness=freshness)
