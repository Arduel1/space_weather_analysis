import streamlit as st

from streamlit_app import data
from streamlit_app.plots.cme import plot_cmes
from streamlit_app.plots.generic import plot_generic
from streamlit_app.plots.kp import plot_kp_index
from streamlit_app.plots.particles import plot_particle_flux
from streamlit_app.plots.sunspots import plot_sunspots
from streamlit_app.plots.xray import plot_xray_flux
from streamlit_app.sidebar import time_range_filter

start_dt, end_dt = time_range_filter()

st.sidebar.title("Data Selection")

available_collections = data.list_stored_collections()
if not available_collections:
    st.sidebar.error("No collections found!")
    st.warning("Database appears empty. Please run the pipeline service first.")
    st.stop()

selected_collection = st.sidebar.selectbox("Select Dataset", available_collections)

st.title(f"Analysis: {selected_collection}")

with st.spinner("Loading data from MongoDB..."):
    df = data.read_collection_range(selected_collection, start_dt, end_dt)

meta = data.get_metadata_for_collection(selected_collection)
if meta:
    with st.expander("Dataset Metadata"):
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
    elif "kp" in coll_lower:
        plot_kp_index(df)
    else:
        plot_generic(df, selected_collection)

    with st.expander("Raw Data Table"):
        st.dataframe(df)
