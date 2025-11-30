from datetime import datetime, timedelta, timezone

import streamlit as st

from streamlit_app.data_loader import DataLoader
from streamlit_app.plots.cme import plot_cmes
from streamlit_app.plots.generic import plot_generic
from streamlit_app.plots.sunspots import plot_sunspots

# Page Configuration
st.set_page_config(
    page_title="Space Weather Analytics",
    page_icon="🌌",
    layout="wide",
    initial_sidebar_state="expanded",
)


# Main App Logic - Singleton Loader
@st.cache_resource
def get_loader():
    """Returns a singleton instance of the DataLoader."""
    return DataLoader()


loader = get_loader()

# Sidebar Data Control
st.sidebar.title("Data Control")

available_collections = loader.get_available_collections()
if not available_collections:
    st.sidebar.error("No collections found!")
    st.warning("Database appears empty. Please run the pipeline service first.")
    st.stop()

selected_collection = st.sidebar.selectbox("Select Dataset", available_collections)

# Time Range Filter Settings
st.sidebar.subheader("Time Range Filter")

# Define the absolute limits for the date picker to allow selection back to 1800
min_allowed_date = datetime(1800, 1, 1)
max_allowed_date = datetime.now() + timedelta(days=365)  # Allow slightly into future

# Defaults set to show the last year of data
default_end = datetime.now()
default_start = default_end - timedelta(days=365)

# Streamlit date_input with explicit min/max to fix the 10-year limit issue
start_date = st.sidebar.date_input("Start Date", value=default_start, min_value=min_allowed_date, max_value=max_allowed_date)
end_date = st.sidebar.date_input("End Date", value=default_end, min_value=min_allowed_date, max_value=max_allowed_date)

# Convert to timezone-aware datetime for the DataLoader/MongoDB
start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
end_dt = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc)

# Main Content Area
st.title(f"Analysis: {selected_collection}")

with st.spinner("Loading data from MongoDB..."):
    # limit=0 ensures we get ALL data for the period
    df = loader.get_timeseries_data(selected_collection, start_time=start_dt, end_time=end_dt, limit=0)

meta = loader.get_metadata(selected_collection)
if meta:
    with st.expander("ℹ️ Dataset Metadata"):
        st.json(meta)

if df.empty:
    st.info("No data found for the selected collection and time range.")
else:
    # Router logic to select the appropriate visualization
    coll_lower = selected_collection.lower()

    if "silso" in coll_lower or "sunspot" in coll_lower:
        plot_sunspots(df, selected_collection)
    elif "cme" in coll_lower:
        plot_cmes(df, selected_collection)
    else:
        plot_generic(df, selected_collection)

    with st.expander("Raw Data Table"):
        st.dataframe(df)

# Footer
st.markdown("---")
st.caption("Space Weather Analysis Platform")