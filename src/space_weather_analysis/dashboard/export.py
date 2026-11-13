import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

from space_weather_analysis.dashboard.about import DatasetAbout, dataset_about
from space_weather_analysis.dashboard.config import DashboardConfig
from space_weather_analysis.dashboard.freshness import DELAYED_FACTOR
from space_weather_analysis.dashboard.labels import Metadata, attribution
from space_weather_analysis.dashboard.panel_types import PANEL_TYPES
from space_weather_analysis.dashboard.panels.base import PAYLOAD, PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.reading import DashboardReader

logger = logging.getLogger(__name__)


class PanelEntry(BaseModel):
    """One panel in the layout of a dashboard document.

    Attributes:
        id: The ID of the panel, which names its payload.
        type: The panel type.
        width: The width of the panel relative to the other panels of its row.
        below: Whether the panel is drawn below the previous panel of its row, in the same column.
        credits: The positions in the credits of the layout of the sources and formulas the panel shows, its footnotes.
    """

    model_config = PAYLOAD

    id: str
    type: str
    width: float
    below: bool = False
    credits: list[int] = Field(default_factory=list)


class Credit(BaseModel):
    """The terms and requested acknowledgement of one source of the shown datasets.

    Attributes:
        source: The provider with license and DOI, or the publication of a formula the dashboard computes with.
        acknowledgement: The acknowledgement text requested by the provider; empty for none.
        citation: The citation text given by the provider; empty for none.
    """

    model_config = PAYLOAD

    source: str
    acknowledgement: str = ""
    citation: str = ""


def credit_of(record: Metadata) -> Credit:
    """Builds the credit of the source of one dataset from its metadata record."""
    citation = record.get("citation", {})
    return Credit(source=attribution(record), acknowledgement=citation.get("acknowledgement") or "", citation=citation.get("text") or "")


def formula_references(payload: Any) -> Iterator[str]:
    """Finds the publication of every formula a payload was computed with: each non-empty `reference` text in it."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "reference" and isinstance(value, str) and value:
                yield value
            else:
                yield from formula_references(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from formula_references(item)


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
        The layout with the credits of every shown dataset and formula, and the payloads by panel ID.
    """
    payloads: dict[str, PanelPayload] = {}
    credits: list[Credit] = []
    pages = [
        PageEntry(title=title, rows=_build_rows(page_number, page_rows, reader, context, payloads, credits))
        for page_number, (title, page_rows) in enumerate(config.pages())
    ]
    return DashboardLayout(generated_at=context.now, pages=pages, credits=credits), payloads


def _footnotes(panel: PanelConfig, payload: PanelPayload, reader: DashboardReader, credits: list[Credit]) -> list[int]:
    """Numbers the sources and formulas of a panel in the credits, adding the ones not listed yet in the order they first appear.

    Returns:
        The footnote positions of the panel in ascending order.
    """
    panel_credits = [credit_of(reader.metadata(dataset)) for dataset in sorted(panel.datasets())]
    panel_credits += [Credit(source=reference) for reference in formula_references(payload.model_dump())]
    footnotes: list[int] = []
    # Credits compare by value, so a source shown by several panels is listed once and shares its footnote
    for credit in panel_credits:
        if credit not in credits:
            credits.append(credit)
        position = credits.index(credit)
        if position not in footnotes:
            footnotes.append(position)
    return sorted(footnotes)


def _build_rows(
    page_number: int,
    rows: list[list[PanelConfig]],
    reader: DashboardReader,
    context: PanelContext,
    payloads: dict[str, PanelPayload],
    credits: list[Credit],
) -> list[list[PanelEntry]]:
    """Builds the payload of every panel of one page into payloads, its credits into credits, and returns the layout entries of its rows."""
    entries = []
    for row_number, row in enumerate(rows):
        row_entries = []
        for column_number, panel in enumerate(row):
            payload = _built_payload(panel, reader, context)
            entry = PanelEntry(
                id=panel_id(page_number, row_number, column_number),
                type=panel.type,
                width=panel.width,
                below=panel.below,
                credits=_footnotes(panel, payload, reader, credits),
            )
            # The info view of every panel describes its datasets, also of a panel whose build failed
            payloads[entry.id] = payload.model_copy(update={"about": _about(panel, reader)})
            row_entries.append(entry)
        entries.append(row_entries)
    return entries


class PanelFailure(BaseModel):
    """Why a panel could not be built.

    Attributes:
        message: The reason in words.
    """

    model_config = PAYLOAD

    message: str


class FailedPanelPayload(PanelPayload):
    """The JSON document of a panel whose build failed, which the frontend shows as a note in place of the panel.

    Attributes:
        error: Why the build failed.
    """

    error: PanelFailure


def _built_payload(panel: PanelConfig, reader: DashboardReader, context: PanelContext) -> PanelPayload:
    """Builds the payload of one panel; a panel whose build fails gets a failure payload, so that the other panels still show.

    Raises:
        pymongo.errors.PyMongoError: If the database cannot be read, which fails every panel alike.
    """
    try:
        return PANEL_TYPES[panel.type].build(panel, reader, context)
    except PyMongoError:
        raise
    except Exception as error:
        # A fault in the data or the code of one panel type must not leave the whole dashboard without new documents
        logger.exception("Panel %s of type %s could not be built", panel.title, panel.type)
        return FailedPanelPayload(type=panel.type, error=PanelFailure(message=f"{type(error).__name__}: {error}"))


def _about(panel: PanelConfig, reader: DashboardReader) -> list[DatasetAbout]:
    """Maps the metadata record of every dataset of a panel with its shown fields; datasets without record are left out."""
    readable = (dataset_about(dataset, reader.metadata(dataset), fields) for dataset, fields in panel.shown_fields().items())
    return [about for about in readable if about is not None]
