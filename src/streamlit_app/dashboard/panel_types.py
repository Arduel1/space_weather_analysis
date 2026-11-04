from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from streamlit_app.dashboard.panels.base import PanelConfig
from streamlit_app.dashboard.panels.table import TablePanel, render_table
from streamlit_app.dashboard.panels.timeseries import TimeseriesPanel, render_timeseries
from streamlit_app.dashboard.panels.value import ValuePanel, render_value


@dataclass(frozen=True)
class PanelType[P: PanelConfig]:
    """A kind of dashboard panel: the model of its settings and the function drawing it.

    Attributes:
        model: Validates the settings of one panel of this type.
        render: Draws a panel from its settings, the time of the drawing and a key unique on the page.
    """

    model: type[P]
    render: Callable[[P, datetime, str], None]


# Panel types by the name a panel gives as `type`; a new kind of visualisation is one module in panels/ and one entry here
PANEL_TYPES: Mapping[str, PanelType[Any]] = MappingProxyType(
    {
        "value": PanelType(ValuePanel, render_value),
        "timeseries": PanelType(TimeseriesPanel, render_timeseries),
        "table": PanelType(TablePanel, render_table),
    }
)
