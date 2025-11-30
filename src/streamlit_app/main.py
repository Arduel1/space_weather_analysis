from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from streamlit_app.data_loader import DataLoader

# --- Page Configuration ---
st.set_page_config(page_title="Space Weather Analytics", page_icon="🌌", layout="wide")


# --- Singleton Loader ---
@st.cache_resource
def get_loader():
    """
    Returns a cached instance of the DataLoader to avoid reconnecting
    to the DB on every rerun.
    """
    return DataLoader()


loader = get_loader()

# --- Sidebar: Controls ---
st.sidebar.title("Data Selection")

#Dataset Selection
available_collections = loader.get_available_collections()
if not available_collections:
    st.error("No datasets found in the database. Ensure the Pipeline has run.")
    st.stop()

selected_collection = st.sidebar.selectbox("Select Dataset / Collection", available_collections)

# Time Range Selection
# Default to last 7 days roughly, or just fixed dates for testing
today = datetime.now(timezone.utc)
default_start = today - timedelta(days=30)
default_end = today

st.sidebar.subheader("Time Range Filter")
start_date = st.sidebar.date_input("Start Date", value=default_start.date())
end_date = st.sidebar.date_input("End Date", value=default_end.date())

# Convert to datetime objects for DB query (start of day, end of day)
start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
end_dt = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc)

# Limit Control
limit = st.sidebar.number_input("Max Records", min_value=100, max_value=50000, value=5000, step=1000)

# --- Main Page ---
st.title(f"Analysis: {selected_collection}")

# Fetch Data
with st.spinner("Fetching data from MongoDB..."):
    df = loader.get_timeseries_data(selected_collection, start_time=start_dt, end_time=end_dt, limit=limit)

# Fetch Metadata
metadata = loader.get_metadata(selected_collection)

# --- Metadata Display ---
with st.expander("Dataset Metadata"):
    if metadata:
        st.json(metadata)
    else:
        st.info("No detailed metadata available for this collection.")

# --- Data Visualization ---
if df.empty:
    st.warning(f"No data found for {selected_collection} in the selected time range.")
else:
    st.markdown(f"**Records loaded:** {len(df)}")

    # Data Table Preview
    with st.expander("Raw Data Preview"):
        st.dataframe(df.head(50))

    # Variable Selection for Plotting
    # Identify numeric columns for plotting
    numeric_cols = df.select_dtypes(include=["float", "int"]).columns.tolist()

    if numeric_cols:
        st.subheader("Interactive Time Series Plot")
        selected_vars = st.multiselect("Select Variables to Plot", options=numeric_cols, default=numeric_cols[:1] if numeric_cols else None)

        if selected_vars:
            # Use Plotly for interactive chart
            fig = px.line(
                df, x=df.index, y=selected_vars, title=f"{selected_collection} - Time Series", labels={"timestamp": "Time", "value": "Measurement"}
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Select at least one variable to plot.")
    else:
        st.warning("No numeric variables found to plot.")

# --- Footer ---
st.markdown("---")
st.caption("Space Weather Analysis Platform | Local Dev Environment")
