from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from space_weather_analysis.dashboard.freshness import DatasetFreshness, dataset_freshness
from space_weather_analysis.dashboard.labels import Metadata, dataset_title, field_label, sources_line
from space_weather_analysis.dashboard.panels.base import CLOSED, PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.util.time_utils import IsoDuration

TIME_LABEL = "Time (UTC)"


class ColumnConfig(BaseModel):
    """One column of a table panel.

    Attributes:
        field: The data field.
        label: The column heading; empty takes the label of the field from the metadata, else the field name.
    """

    model_config = CLOSED

    field: str = Field(min_length=1)
    label: str = ""


class TablePanel(PanelConfig):
    """A table of the newest documents of one dataset.

    Attributes:
        dataset: The dataset ID, which must belong to a feed.
        columns: The columns after the time column, in this order.
        history: The span searched for documents, ending with the newest stored document.
        count: The number of documents shown.
    """

    dataset: str = Field(min_length=1)
    columns: list[ColumnConfig] = Field(min_length=1)
    history: IsoDuration
    count: int = Field(default=20, gt=0)

    def fields(self) -> tuple[str, ...]:
        """Lists the data fields of the columns in their order."""
        return tuple(column.field for column in self.columns)


def table_frame(panel: TablePanel, frame: pd.DataFrame, metadata: Metadata) -> pd.DataFrame:
    """Selects the newest documents as a frame for display.

    Args:
        panel: The panel settings.
        frame: The loaded documents with a UTC DatetimeIndex.
        metadata: The metadata record of the dataset; empty if it has none.

    Returns:
        The newest documents first with the time as first column and the configured columns that exist in the frame.
    """
    shown = [column for column in panel.columns if column.field in frame.columns]
    newest = frame.sort_index(ascending=False).head(panel.count)[[column.field for column in shown]]
    newest = newest.rename(columns={column.field: field_label(metadata, column.field, column.label) for column in shown})
    newest.insert(0, TIME_LABEL, newest.index)
    return newest.reset_index(drop=True)


class TablePayload(PanelPayload):
    """The JSON document of a table.

    Attributes:
        title: The heading; empty if neither configuration nor metadata give one.
        columns: The column headings, the time first.
        rows: The newest documents first, one value per column, null where a document lacks the field.
        source: The source line of the dataset.
        freshness: How current the dataset is; empty without stored documents.
    """

    title: str
    columns: list[str]
    rows: list[list[Any]]
    source: str
    freshness: list[DatasetFreshness]


def build_table(panel: TablePanel, reader: DashboardReader, context: PanelContext) -> TablePayload:
    """Builds the JSON document of a table from the newest documents of its dataset.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export, with the feed interval of each dataset.

    Returns:
        The payload.
    """
    frame = reader.recent(panel.dataset, panel.history, panel.fields())
    metadata = reader.metadata(panel.dataset)
    if frame.empty:
        freshness = []
        # Every configured column, so that an empty table still shows its headings
        frame = pd.DataFrame(columns=list(panel.fields()), index=pd.DatetimeIndex([], tz="UTC"))
    else:
        freshness = [dataset_freshness(panel.dataset, frame.index.max().to_pydatetime(), metadata, context.feed_intervals.get(panel.dataset))]
    shown = table_frame(panel, frame, metadata)
    # Python datetimes, which serialize as ISO 8601 text unlike the pandas time stamps of an object column
    times = pd.DatetimeIndex(shown[TIME_LABEL]).to_pydatetime().tolist()
    fields = shown.drop(columns=TIME_LABEL)
    values = fields.astype(object).where(fields.notna(), None).to_numpy().tolist()
    return TablePayload(
        type=panel.type,
        title=dataset_title(metadata, panel.title),
        columns=[str(column) for column in shown.columns],
        rows=[[time, *row] for time, row in zip(times, values, strict=True)],
        source=sources_line([metadata]),
        freshness=freshness,
    )
