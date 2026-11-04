import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import streamlit as st
from pydantic import Field

from streamlit_app import data
from streamlit_app.dashboard.panels.base import PanelConfig, ScaleChoice, ValueFormat, collection_of
from streamlit_app.dashboard.scales import Level, format_value


class ValuePanel(PanelConfig, ScaleChoice, ValueFormat):
    """A tile with the newest value of one field and, with a scale, its level.

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
        text: The formatted value with unit, "n/a" without a value.
        time: The UTC time stamp of the value; None without a value.
        level: The level of the value; None without scale, value or level.
    """

    text: str
    time: datetime | None
    level: Level | None


def value_view(panel: ValuePanel, latest: tuple[datetime, dict[str, Any]] | None) -> ValueView:
    """Builds what a value tile shows from the newest document of its dataset.

    Args:
        panel: The panel settings.
        latest: The time stamp and data fields of the newest document; None if there is none.

    Returns:
        The view; a missing, non-numeric or non-finite value shows "n/a" without level.
    """
    if latest is None:
        return ValueView("n/a", None, None)
    time, fields = latest
    value = fields.get(panel.field)
    # bool is a subclass of int but no measurement
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return ValueView("n/a", time, None)
    text = format_value(float(value), panel.format)
    scale = panel.resolved_scale()
    return ValueView(f"{text} {panel.unit}".rstrip(), time, scale.level_of(float(value)) if scale is not None else None)


def render_value(panel: ValuePanel, now: datetime, key: str) -> None:
    """Shows the tile with its value, level badge and time stamp.

    Args:
        panel: The panel settings.
        now: The time of the drawing.
        key: The key of the panel elements, unique on the page.
    """
    del now, key
    view = value_view(panel, data.read_latest(collection_of(panel.dataset), (panel.field,)))
    with st.container(border=True):
        st.metric(panel.title or panel.field, view.text)
        if view.level is not None:
            st.html(
                f'<span style="background:{view.level.color};color:#000;border-radius:4px;padding:1px 8px;font-weight:600">{view.level.label}</span>'
            )
        st.caption(f"{view.time:%Y-%m-%d %H:%M} UTC" if view.time is not None else "No data stored")
