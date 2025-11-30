from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from streamlit_app.data_loader import DataLoader
from streamlit_app.plots.cme import plot_cmes
from streamlit_app.plots.correlation import plot_solar_cycle_correlation
from streamlit_app.plots.generic import plot_generic
from streamlit_app.plots.particles import plot_particle_flux
from streamlit_app.plots.sunspots import plot_sunspots
from streamlit_app.plots.xray import plot_xray_flux

# Page Configuration
st.set_page_config(
    page_title="Space Weather Analytics",
    page_icon="🌌",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_loader() -> DataLoader:
    """Returns a singleton instance of the DataLoader."""
    return DataLoader()


loader = get_loader()

# Sidebar Navigation
st.sidebar.title("Navigation")
app_mode = st.sidebar.radio("Choose Mode", ["Dataset Explorer", "Cycle Correlation Analysis"])

st.sidebar.markdown("---")

# Global Time Range Filter
st.sidebar.subheader("Time Range Filter")

min_allowed_date = datetime(1800, 1, 1)
max_allowed_date = datetime.now() + timedelta(days=365)

default_end = datetime.now()
default_start = default_end - timedelta(days=365)

start_date = st.sidebar.date_input("Start Date", value=default_start, min_value=min_allowed_date, max_value=max_allowed_date)
end_date = st.sidebar.date_input("End Date", value=default_end, min_value=min_allowed_date, max_value=max_allowed_date)

# Convert to timezone-aware datetime
start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
end_dt = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc)


if app_mode == "Dataset Explorer":
    # Single Dataset Analysis Mode
    st.sidebar.title("Data Selection")

    available_collections = loader.get_available_collections()
    if not available_collections:
        st.sidebar.error("No collections found!")
        st.warning("Database appears empty. Please run the pipeline service first.")
        st.stop()

    selected_collection = st.sidebar.selectbox("Select Dataset", available_collections)

    st.title(f"Analysis: {selected_collection}")

    with st.spinner("Loading data from MongoDB..."):
        df = loader.get_timeseries_data(selected_collection, start_time=start_dt, end_time=end_dt, limit=0)

    meta = loader.get_metadata(selected_collection)
    if meta:
        with st.expander("ℹDataset Metadata"):
            st.json(meta)

    if df.empty:
        st.info("No data found for the selected collection and time range.")
    else:
        coll_lower = selected_collection.lower()

        if "silso" in coll_lower or "sunspot" in coll_lower:
            plot_sunspots(df, selected_collection)
        elif "cme" in coll_lower:
            plot_cmes(df, selected_collection)
        elif "xray" in coll_lower:
            plot_xray_flux(df, selected_collection, key_suffix="explorer")
        elif "part" in coll_lower or "flux" in coll_lower:
            plot_particle_flux(df, selected_collection, key_suffix="explorer")
        else:
            plot_generic(df, selected_collection)

        with st.expander("Raw Data Table"):
            st.dataframe(df)

elif app_mode == "Cycle Correlation Analysis":
    # Multi-Dataset Correlation Mode
    st.title("Solar Cycle & Event Correlation")
    st.markdown("""
    This view combines the **SILSO Sunspot Number** (representing the solar magnetic cycle) 
    and the **SOHO/LASCO CME Catalog** (representing impulsive solar events) to visualize relationships.
    """)  # noqa: W291

    target_sunspot = "silso_sunspot_daily"
    target_cme = "cme_catalog"
    target_xray = "goesp_xray_flux_P1M"
    target_part = "goesp_part_flux_P5M"

    available = loader.get_available_collections()

    if target_sunspot in available and target_cme in available:
        with st.spinner("Loading datasets..."):
            df_sunspot = loader.get_timeseries_data(target_sunspot, start_time=start_dt, end_time=end_dt)
            df_cme = loader.get_timeseries_data(target_cme, start_time=start_dt, end_time=end_dt)

            # Load X-Ray if available
            df_xray = pd.DataFrame()
            if target_xray in available:
                df_xray = loader.get_timeseries_data(target_xray, start_time=start_dt, end_time=end_dt)

            # Load Particles if available
            df_part = pd.DataFrame()
            if target_part in available:
                df_part = loader.get_timeseries_data(target_part, start_time=start_dt, end_time=end_dt)

        if not df_sunspot.empty and not df_cme.empty:
            plot_solar_cycle_correlation(df_cme, df_sunspot)
            if not df_xray.empty:
                st.markdown("---")
                plot_xray_flux(df_xray, target_xray, key_suffix="corr")
            elif target_xray in available:
                st.warning("X-Ray data available but no data found in selected range.")
            if not df_part.empty:
                st.markdown("---")
                plot_particle_flux(df_part, target_part, key_suffix="corr")
            elif target_part in available:
                st.warning("Particle data available but no data found in selected range.")
            else:
                st.info(f"X-Ray dataset ({target_xray}) not found in database. Run pipeline to enable flare analysis.")

            with st.expander("View Merged Data Statistics"):
                cols = st.columns(4)
                cols[0].metric("Total CME Events", len(df_cme))
                cols[1].metric("Days of Sunspot Data", len(df_sunspot))
                cols[2].metric("X-Ray Data Points", len(df_xray) if not df_xray.empty else 0)
                cols[3].metric("Particle Data Points", len(df_part) if not df_part.empty else 0)
        else:
            st.error(f"Data missing for the selected timeframe. Sunspots: {len(df_sunspot)} rows, CMEs: {len(df_cme)} rows.")
    else:
        st.error(f"Required datasets not found. Needed: '{target_sunspot}' and '{target_cme}'. Available: {available}")

# Footer
st.markdown("---")
st.caption("Space Weather Analysis Platform")
