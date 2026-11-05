from datetime import datetime

from pydantic import BaseModel

from space_weather_analysis.dashboard.config import DashboardConfig
from space_weather_analysis.dashboard.freshness import DELAYED_FACTOR
from space_weather_analysis.dashboard.labels import Metadata, attribution
from space_weather_analysis.dashboard.panel_types import PANEL_TYPES
from space_weather_analysis.dashboard.panels.base import PAYLOAD, PanelConfig, PanelContext, PanelPayload
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


class Credit(BaseModel):
    """The terms and requested acknowledgement of one source of the shown datasets.

    Attributes:
        source: The provider with license and DOI, as the panels name it.
        acknowledgement: The acknowledgement text requested by the provider; empty for none.
        citation: The citation text given by the provider; empty for none.
    """

    model_config = PAYLOAD

    source: str
    acknowledgement: str = ""
    citation: str = ""


def credits_of(records: list[Metadata]) -> list[Credit]:
    """Lists the credit of every source of the given metadata records, each once, sorted by source.

    Args:
        records: The metadata records of the shown datasets.

    Returns:
        The credits.
    """
    unique = {
        Credit(
            source=attribution(record),
            acknowledgement=record.get("citation", {}).get("acknowledgement") or "",
            citation=record.get("citation", {}).get("text") or "",
        )
        for record in records
    }
    return sorted(unique, key=lambda credit: (credit.source, credit.acknowledgement, credit.citation))


class PageEntry(BaseModel):
    """One page in the layout of a dashboard document.

    Attributes:
        title: The tab title.
        rows: The rows from the top, each with its panels from the left.
    """

    model_config = PAYLOAD

    title: str
    rows: list[list[PanelEntry]]


class DashboardLayout(BaseModel):
    """The JSON document that lays out the panels of a dashboard.

    Attributes:
        generated_at: When the payloads were built.
        delayed_factor: Multiple of the expected age up to which data count as delayed instead of stale.
        pages: The pages in tab order.
        credits: The terms and acknowledgements of the sources of the shown datasets.
    """

    model_config = PAYLOAD

    generated_at: datetime
    delayed_factor: int = DELAYED_FACTOR
    pages: list[PageEntry]
    credits: list[Credit]


def panel_id(page_number: int, row_number: int, column_number: int) -> str:
    """Names a panel by its page, row and column, all counted from 0."""
    return f"panel-{page_number}-{row_number}-{column_number}"


def build_dashboard(config: DashboardConfig, reader: DashboardReader, context: PanelContext) -> tuple[DashboardLayout, dict[str, PanelPayload]]:
    """Builds the layout and the payload of every panel of a dashboard.

    Args:
        config: What the dashboard shows.
        reader: The source of documents, metadata and job summaries.
        context: The context of the export, whose time is the build time.

    Returns:
        The layout with the credits of every shown dataset, and the payloads by panel ID.
    """
    payloads: dict[str, PanelPayload] = {}
    pages = [
        PageEntry(title=title, rows=_build_rows(page_number, page_rows, reader, context, payloads))
        for page_number, (title, page_rows) in enumerate(config.pages())
    ]
    credits = credits_of([reader.metadata(dataset) for dataset in sorted(config.datasets())])
    return DashboardLayout(generated_at=context.now, pages=pages, credits=credits), payloads


def _build_rows(
    page_number: int, rows: list[list[PanelConfig]], reader: DashboardReader, context: PanelContext, payloads: dict[str, PanelPayload]
) -> list[list[PanelEntry]]:
    """Builds the payload of every panel of one page into payloads and returns the layout entries of its rows."""
    entries = []
    for row_number, row in enumerate(rows):
        row_entries = []
        for column_number, panel in enumerate(row):
            entry = PanelEntry(id=panel_id(page_number, row_number, column_number), type=panel.type, width=panel.width)
            payloads[entry.id] = PANEL_TYPES[panel.type].build(panel, reader, context)
            row_entries.append(entry)
        entries.append(row_entries)
    return entries
