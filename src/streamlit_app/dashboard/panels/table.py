from datetime import datetime

import pandas as pd
import streamlit as st
from pydantic import BaseModel, Field

from space_weather_analysis.util.time_utils import IsoDuration
from streamlit_app import data
from streamlit_app.dashboard.labels import Metadata, dataset_title, field_label, sources_line
from streamlit_app.dashboard.panels.base import CLOSED, PANEL_HEIGHT, PanelConfig, collection_of

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


def render_table(panel: TablePanel, now: datetime, key: str) -> None:
    """Shows the table at the panel height, or a note if the dataset holds no documents.

    Args:
        panel: The panel settings.
        now: The time of the drawing.
        key: The key of the table element, unique on the page.
    """
    del now
    frame = data.read_recent(collection_of(panel.dataset), panel.history, tuple(column.field for column in panel.columns))
    metadata = data.get_metadata(panel.dataset)
    with st.container(border=True, height=PANEL_HEIGHT):
        title = dataset_title(metadata, panel.title)
        if title:
            st.markdown(f"**{title}**")
        st.caption(sources_line([metadata]))
        if frame.empty:
            st.info(f"No data stored for {panel.dataset}.")
            return
        st.dataframe(table_frame(panel, frame, metadata), hide_index=True, key=key)
