from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from space_weather_analysis.dashboard.panels.base import PanelContext
from streamlit_app.dashboard.panels.feeds import render_feeds
from streamlit_app.dashboard.panels.table import render_table
from streamlit_app.dashboard.panels.timeseries import render_timeseries
from streamlit_app.dashboard.panels.value import render_value

# Function drawing a panel from its settings, the context of the drawing and a key unique on the page
type PanelRenderer = Callable[[Any, PanelContext, str], None]

# Drawing function of each panel type of the core panel type registry, by type name
PANEL_RENDERERS: Mapping[str, PanelRenderer] = MappingProxyType(
    {
        "value": render_value,
        "timeseries": render_timeseries,
        "table": render_table,
        "feeds": render_feeds,
    }
)
