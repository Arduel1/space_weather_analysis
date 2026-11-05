import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import streamlit as st
from pydantic import Field

from streamlit_app import data
from streamlit_app.dashboard.freshness import panel_freshness
from streamlit_app.dashboard.labels import HELP_LINE_BREAK, Metadata, dataset_title, field_description, field_label, field_unit, sources_line
from streamlit_app.dashboard.panels.base import PanelConfig, PanelContext, ScaleChoice, ValidityChoice, ValueFormat, collection_of
from streamlit_app.dashboard.scales import FORMATTERS, Level, format_value
from streamlit_app.dashboard.validity import describe_validity, valid_range


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
        help: The description of the field and the title of its dataset, shown as tooltip; empty without either.
        source: The source line of the dataset.
    """

    title: str
    text: str
    time: datetime | None
    level: Level | None
    help: str = ""
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
    help_text = HELP_LINE_BREAK.join(_help_lines(panel, metadata))
    labels = {"title": title, "help": help_text, "source": sources_line([metadata])}
    if latest is None:
        return ValueView(text="n/a", time=None, level=None, **labels)
    time, fields = latest
    value = fields.get(panel.field)
    # bool is a subclass of int but no measurement
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return ValueView(text="n/a", time=time, level=None, **labels)
    if panel.hide_invalid and not valid_range(metadata, panel.field, panel.valid_min, panel.valid_max).is_valid(float(value)):
        return ValueView(text="invalid", time=time, level=None, **labels)
    # A named formatter writes a complete text such as a flare class, which takes no unit
    unit = "" if panel.format in FORMATTERS else field_unit(metadata, panel.field, panel.unit)
    text = f"{format_value(float(value), panel.format)} {unit}".rstrip()
    scale = panel.resolved_scale()
    return ValueView(text=text, time=time, level=scale.level_of(float(value)) if scale is not None else None, **labels)


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


def render_value(panel: ValuePanel, context: PanelContext, key: str) -> None:
    """Shows the tile with its value, level badge, time stamp with freshness, and source.

    Args:
        panel: The panel settings.
        context: The context of the drawing.
        key: The key of the panel elements, unique on the page.
    """
    del key
    metadata = data.get_metadata(panel.dataset)
    view = value_view(panel, data.read_latest(collection_of(panel.dataset), (panel.field,)), metadata)
    with st.container(border=True):
        st.metric(view.title, view.text, help=view.help or None)
        if view.level is not None:
            st.html(
                f'<span style="background:{view.level.color};color:#000;border-radius:4px;padding:1px 8px;font-weight:600">{view.level.label}</span>'
            )
        if view.time is None:
            st.caption("No data stored")
        else:
            state = panel_freshness({panel.dataset: view.time}, {panel.dataset: metadata}, context.feed_intervals, context.now)
            st.caption(f"{view.time:%Y-%m-%d %H:%M} UTC, {state}")
        st.caption(view.source)
