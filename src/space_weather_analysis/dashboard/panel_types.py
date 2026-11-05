from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from space_weather_analysis.dashboard.panels.base import PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.panels.feeds import FeedsPanel, build_feeds
from space_weather_analysis.dashboard.panels.table import TablePanel, build_table
from space_weather_analysis.dashboard.panels.timeseries import TimeseriesPanel, build_timeseries
from space_weather_analysis.dashboard.panels.value import ValuePanel, build_value
from space_weather_analysis.dashboard.reading import DashboardReader


@dataclass(frozen=True)
class PanelType[P: PanelConfig]:
    """A kind of dashboard panel: the model of its settings and the function building its JSON document.

    Attributes:
        model: Validates the settings of one panel of this type.
        build: Builds the payload of a panel from its settings, the reads and the context of the export.
    """

    model: type[P]
    build: Callable[[P, DashboardReader, PanelContext], PanelPayload]


# Panel types by the name a panel gives as `type`; a new kind of visualisation is one module in panels/, one entry
# here and a drawing function in each frontend
PANEL_TYPES: Mapping[str, PanelType[Any]] = MappingProxyType(
    {
        "value": PanelType(ValuePanel, build_value),
        "timeseries": PanelType(TimeseriesPanel, build_timeseries),
        "table": PanelType(TablePanel, build_table),
        "feeds": PanelType(FeedsPanel, build_feeds),
    }
)
