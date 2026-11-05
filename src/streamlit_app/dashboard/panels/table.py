import streamlit as st

from space_weather_analysis.dashboard.labels import dataset_title, sources_line
from space_weather_analysis.dashboard.panels.base import PanelContext, collection_of
from space_weather_analysis.dashboard.panels.table import TablePanel, table_frame
from streamlit_app import data
from streamlit_app.dashboard.drawing import PANEL_HEIGHT, panel_freshness


def render_table(panel: TablePanel, context: PanelContext, key: str) -> None:
    """Shows the table at the panel height with freshness and source, or a note if the dataset holds no documents.

    Args:
        panel: The panel settings.
        context: The context of the drawing.
        key: The key of the table element, unique on the page.
    """
    frame = data.read_recent(collection_of(panel.dataset), panel.history, panel.fields())
    metadata = data.get_metadata(panel.dataset)
    with st.container(border=True, height=PANEL_HEIGHT):
        title = dataset_title(metadata, panel.title)
        if title:
            st.markdown(f"**{title}**")
        if frame.empty:
            st.caption(sources_line([metadata]))
            st.info(f"No data stored for {panel.dataset}.")
            return
        state = panel_freshness({panel.dataset: frame.index.max().to_pydatetime()}, {panel.dataset: metadata}, context.feed_intervals, context.now)
        st.caption(f"{state} -- {sources_line([metadata])}")
        st.dataframe(table_frame(panel, frame, metadata), hide_index=True, key=key)
