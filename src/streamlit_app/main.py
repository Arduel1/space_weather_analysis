import time
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from typing import Optional, Tuple

import pandas as pd
import streamlit as st
from dateutil import parser

from streamlit_app.data_loader import DataLoader
from streamlit_app.plots.cme import plot_cmes
from streamlit_app.plots.correlation import plot_solar_cycle_correlation
from streamlit_app.plots.generic import plot_generic
from streamlit_app.plots.kp import plot_kp_index
from streamlit_app.plots.particles import plot_particle_flux
from streamlit_app.plots.sunspots import plot_sunspots
from streamlit_app.plots.xray import plot_xray_flux

# Define global constants for date input constraints to prevent Streamlit
# from defaulting to a narrow +/- 10 year range.
MIN_ALLOWED_DATE = datetime(1800, 1, 1)
MAX_ALLOWED_DATE = datetime(2200, 1, 1)


def parse_catalog_time(time_str: Optional[str], default: datetime) -> datetime:
    """
    Parses a time string from the catalog into a timezone-aware datetime object.

    Args:
        time_str: The ISO formatted time string (e.g., '2000-01-01T00:00:00Z').
        default: A fallback datetime to return if parsing fails or input is None.

    Returns:
        A datetime object with UTC timezone information.
    """
    if not time_str:
        return default.replace(tzinfo=timezone.utc)
    try:
        dt = parser.parse(time_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return default.replace(tzinfo=timezone.utc)


def calculate_default_range(start_dt: datetime, end_dt: datetime) -> Tuple[datetime, datetime]:
    """
    Calculates a default 1-day selection window centered in the given range.

    This ensures that when a user selects a dataset, the initial time picker
    values point to valid data rather than an arbitrary 'today' date.

    Args:
        start_dt: The earliest available data timestamp.
        end_dt: The latest available data timestamp.

    Returns:
        A tuple containing the calculated start and end datetimes.
    """
    total_duration = end_dt - start_dt
    midpoint = start_dt + (total_duration / 2)

    # Define a fixed window of one day starting from the midpoint
    default_start = midpoint
    default_end = midpoint + timedelta(days=1)

    # Ensure the calculated end does not exceed the absolute max limit of the dataset
    if default_end > end_dt:
        default_end = end_dt
        default_start = max(start_dt, default_end - timedelta(days=1))

    return default_start, default_end


# Configure the page layout and appearance
st.set_page_config(
    page_title="Space Weather Analytics",
    page_icon="🌌",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_loader() -> DataLoader:
    """
    Returns a cached singleton instance of the DataLoader.
    """
    return DataLoader()


loader = get_loader()

st.sidebar.title("Navigation")
app_mode = st.sidebar.radio("Choose Mode", ["Dataset Explorer", "Cycle Correlation Analysis", "Data Downloader"])

st.sidebar.markdown("---")

# Initialize shared filter variables to defaults
start_dt = datetime.now(timezone.utc) - timedelta(days=365)
end_dt = datetime.now(timezone.utc)

# Render the sidebar time filter only for analysis modes
# The downloader handles its own specific time selection logic
if app_mode != "Data Downloader":
    st.sidebar.subheader("Time Range Filter")

    # Initialize defaults relative to the current time
    default_global_end = datetime.now()
    default_global_start = default_global_end - timedelta(days=365)

    start_date = st.sidebar.date_input(
        "Start Date",
        value=default_global_start,
        min_value=MIN_ALLOWED_DATE,
        max_value=MAX_ALLOWED_DATE,
    )
    end_date = st.sidebar.date_input(
        "End Date",
        value=default_global_end,
        min_value=MIN_ALLOWED_DATE,
        max_value=MAX_ALLOWED_DATE,
    )

    # Convert date inputs to timezone-aware datetime objects for database compatibility
    start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc)


if app_mode == "Dataset Explorer":
    st.sidebar.title("Data Selection")

    available_collections = loader.get_available_collections()
    if not available_collections:
        st.sidebar.error("No collections found!")
        st.warning("Database appears empty. Please run the pipeline service first.")
        st.stop()

    selected_collection = st.sidebar.selectbox("Select Dataset", available_collections)

    st.title(f"Analysis: {selected_collection}")

    with st.spinner("Loading data from MongoDB..."):
        # Retrieve all data points within the range by setting limit to 0
        df = loader.get_timeseries_data(selected_collection, start_time=start_dt, end_time=end_dt, limit=0)

    meta = loader.get_metadata(selected_collection)
    if meta:
        with st.expander("ℹ Dataset Metadata"):
            st.json(meta)

    if df.empty:
        st.info("No data found for the selected collection and time range.")
    else:
        # Dispatch to the appropriate plotting function based on naming conventions
        coll_lower = selected_collection.lower()

        if "silso" in coll_lower or "sunspot" in coll_lower:
            plot_sunspots(df, selected_collection)
        elif "cme" in coll_lower:
            plot_cmes(df, selected_collection)
        elif "xray" in coll_lower:
            plot_xray_flux(df, selected_collection, key_suffix="explorer")
        elif "part" in coll_lower or "flux" in coll_lower:
            plot_particle_flux(df, selected_collection, key_suffix="explorer")
        elif "kp" in coll_lower:
            plot_kp_index(df)
        else:
            plot_generic(df, selected_collection)

        with st.expander("Raw Data Table"):
            st.dataframe(df)

elif app_mode == "Data Downloader":
    st.title("Data Catalog & Download Center")
    st.markdown(
        """
        Browse the available datasets from the global catalog and trigger new download jobs.
        Once the data is fetched by the pipeline, it will be visualized below.
        """
    )

    catalog = loader.get_master_catalog()
    if not catalog:
        st.error("Master catalog is empty. Please run the Catalog Collector service.")
    else:
        catalog_map = {item["dataset_id"]: item for item in catalog}
        dataset_ids = sorted(list(catalog_map.keys()))

        selected_id = st.selectbox("Select Dataset from Catalog", dataset_ids)

        if selected_id:
            entry = catalog_map[selected_id]
            # Use the first source for configuration defaults
            sources = entry.get("sources", [])
            primary_source = sources[0] if sources else {}

            col1, col2 = st.columns([1, 1])

            with col1:
                st.subheader("Dataset Info")
                full_meta = loader.get_metadata(selected_id)
                if full_meta:
                    metadata_content = full_meta.get("metadata", {})
                    st.json(metadata_content, expanded=False)
                else:
                    st.info("No detailed descriptive metadata available.")

                st.write(f"**Source ID:** {primary_source.get('source_id', 'Unknown')}")
                st.write(f"**Type:** {primary_source.get('type', 'Unknown')}")

            with col2:
                st.subheader("Download Configuration")

                tr = primary_source.get("time_range", {})

                # Parse catalog limits to establish the dataset boundaries
                cat_start_dt = parse_catalog_time(tr.get("start_time"), MIN_ALLOWED_DATE)
                cat_end_dt = parse_catalog_time(tr.get("end_time"), MAX_ALLOWED_DATE)

                st.caption(f"Available Range: {cat_start_dt} to {cat_end_dt}")

                # Determine the initial 1-day selection window centered in the dataset
                init_start, init_end = calculate_default_range(cat_start_dt, cat_end_dt)

                # Configure Start Inputs
                c1, c2 = st.columns(2)
                with c1:
                    req_start_date = st.date_input(
                        "Start Date",
                        value=init_start.date(),
                        min_value=MIN_ALLOWED_DATE,
                        max_value=MAX_ALLOWED_DATE,
                    )
                with c2:
                    req_start_time = st.time_input("Start Time", value=init_start.time())

                # Configure End Inputs
                c3, c4 = st.columns(2)
                with c3:
                    req_end_date = st.date_input(
                        "End Date",
                        value=init_end.date(),
                        min_value=MIN_ALLOWED_DATE,
                        max_value=MAX_ALLOWED_DATE,
                    )
                with c4:
                    req_end_time = st.time_input("End Time", value=init_end.time())

                # Combine Date and Time components into UTC datetimes
                req_start_dt = datetime.combine(req_start_date, req_start_time).replace(tzinfo=timezone.utc)
                req_end_dt = datetime.combine(req_end_date, req_end_time).replace(tzinfo=timezone.utc)

                if st.button("Start Download Job", type="primary"):
                    job_id = loader.create_download_job(
                        dataset_id=selected_id,
                        start_time=req_start_dt,
                        end_time=req_end_dt,
                    )
                    if job_id:
                        st.session_state["current_job_id"] = job_id
                        st.success(f"Job started! ID: {job_id}")
                    else:
                        st.error("Failed to start job.")

            if "current_job_id" in st.session_state:
                st.markdown("---")
                st.subheader("Job Status")

                job_id = st.session_state["current_job_id"]
                status_placeholder = st.empty()
                progress_bar = st.progress(0)

                # Monitor job status via polling
                complete = False
                while not complete:
                    job = loader.get_job_status(job_id)
                    status = job.get("status", "UNKNOWN")

                    progress = job.get("progress", 0.0)
                    if isinstance(progress, (int, float)):
                        val = min(max(float(progress), 0.0), 1.0)
                        progress_bar.progress(val)

                    status_placeholder.info(f"Current Status: **{status}**")

                    if status == "COMPLETED":
                        progress_bar.progress(1.0)
                        st.success("Download Complete!")
                        complete = True
                    elif status == "FAILED":
                        st.error(f"Job Failed: {job.get('error', 'Unknown error')}")
                        complete = True
                        break
                    else:
                        time.sleep(2)

                if complete and job.get("status") == "COMPLETED":
                    st.markdown("### Preview Data")
                    params = job.get("parameters", {})
                    job_start = params.get("start_time")
                    job_end = params.get("end_time")

                    df_new = loader.get_timeseries_data(selected_id, start_time=job_start, end_time=job_end)

                    if not df_new.empty:
                        plot_generic(df_new, selected_id)
                        with st.expander("Raw Data"):
                            st.dataframe(df_new)
                    else:
                        st.warning("Job marked completed but no data found in collection.")

st.markdown("---")
st.caption("Space Weather Analysis Platform")
