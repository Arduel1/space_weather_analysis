from datetime import datetime

from pydantic import BaseModel

from space_weather_analysis.dashboard.config import DashboardConfig
from space_weather_analysis.dashboard.freshness import DELAYED_FACTOR
from space_weather_analysis.dashboard.panel_types import PANEL_TYPES
from space_weather_analysis.dashboard.panels.base import PAYLOAD, PanelContext, PanelPayload
from space_weather_analysis.dashboard.reading import DashboardReader


class PanelEntry(BaseModel):
    """One panel in the layout of a dashboard document.

    Attributes:
        id: The ID of the panel, which names its payload.
        type: The panel type.
        width: The width of the panel relative to the other panels of its row.
    """

    model_config = PAYLOAD

    id: str
    type: str
    width: float


class DashboardLayout(BaseModel):
    """The JSON document that lays out the panels of a dashboard.

    Attributes:
        generated_at: When the payloads were built.
        delayed_factor: Multiple of the expected age up to which data count as delayed instead of stale.
        rows: The rows from the top, each with its panels from the left.
    """

    model_config = PAYLOAD

    generated_at: datetime
    delayed_factor: int = DELAYED_FACTOR
    rows: list[list[PanelEntry]]


def panel_id(row_number: int, column_number: int) -> str:
    """Names a panel by its row and column, both counted from 0."""
    return f"panel-{row_number}-{column_number}"


def build_dashboard(config: DashboardConfig, reader: DashboardReader, context: PanelContext) -> tuple[DashboardLayout, dict[str, PanelPayload]]:
    """Builds the layout and the payload of every panel of a dashboard.

    Args:
        config: What the dashboard shows.
        reader: The source of documents, metadata and job summaries.
        context: The context of the export, whose time is the build time.

    Returns:
        The layout and the payloads by panel ID.
    """
    rows = []
    payloads: dict[str, PanelPayload] = {}
    for row_number, row in enumerate(config.rows()):
        entries = []
        for column_number, panel in enumerate(row):
            entry = PanelEntry(id=panel_id(row_number, column_number), type=panel.type, width=panel.width)
            payloads[entry.id] = PANEL_TYPES[panel.type].build(panel, reader, context)
            entries.append(entry)
        rows.append(entries)
    return DashboardLayout(generated_at=context.now, rows=rows), payloads
