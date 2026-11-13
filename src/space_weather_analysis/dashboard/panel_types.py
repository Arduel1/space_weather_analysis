from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from space_weather_analysis.dashboard.panels.aurora import AuroraPanel, build_aurora
from space_weather_analysis.dashboard.panels.base import PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.panels.butterfly import ButterflyPanel, build_butterfly
from space_weather_analysis.dashboard.panels.chains import ChainsPanel, build_chains
from space_weather_analysis.dashboard.panels.events import EpochPanel, build_epoch
from space_weather_analysis.dashboard.panels.feeds import FeedsPanel, build_feeds
from space_weather_analysis.dashboard.panels.heliosphere import HeliospherePanel, build_heliosphere
from space_weather_analysis.dashboard.panels.images import ImagesPanel, build_images
from space_weather_analysis.dashboard.panels.magnetic import MagneticFieldPanel, build_magnetic_field
from space_weather_analysis.dashboard.panels.magnetic_connection import MagneticConnectionPanel, build_magnetic_connection
from space_weather_analysis.dashboard.panels.messages import MessagesPanel, build_messages
from space_weather_analysis.dashboard.panels.relations import GroupedPanel, build_grouped
from space_weather_analysis.dashboard.panels.solar_cycle import SolarCyclePanel, build_solar_cycle
from space_weather_analysis.dashboard.panels.sun import SunDiskPanel, build_sun_disk
from space_weather_analysis.dashboard.panels.timeseries import StackPanel, build_stack
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
        "stack": PanelType(StackPanel, build_stack),
        "feeds": PanelType(FeedsPanel, build_feeds),
        "images": PanelType(ImagesPanel, build_images),
        "messages": PanelType(MessagesPanel, build_messages),
        "epoch": PanelType(EpochPanel, build_epoch),
        "chains": PanelType(ChainsPanel, build_chains),
        "sundisk": PanelType(SunDiskPanel, build_sun_disk),
        "butterfly": PanelType(ButterflyPanel, build_butterfly),
        "fieldlines": PanelType(MagneticFieldPanel, build_magnetic_field),
        "fieldmap": PanelType(MagneticFieldPanel, build_magnetic_field),
        "heliosphere": PanelType(HeliospherePanel, build_heliosphere),
        "magnetic_connection": PanelType(MagneticConnectionPanel, build_magnetic_connection),
        "aurora": PanelType(AuroraPanel, build_aurora),
        "solarcycle": PanelType(SolarCyclePanel, build_solar_cycle),
        "grouped": PanelType(GroupedPanel, build_grouped),
    }
)
