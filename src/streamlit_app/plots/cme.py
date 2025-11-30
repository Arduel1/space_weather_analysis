import pandas as pd
import plotly.express as px
import streamlit as st


def plot_cmes(df: pd.DataFrame, collection_name: str) -> None:
    """
    Visualizes Coronal Mass Ejection (CME) events using scatter plots to correlate speed and width.
    """
    st.subheader(f"💥 CME Event Analysis: {collection_name}")

    required_cols = ["linear_speed", "width", "central_pa"]
    if not all(col in df.columns for col in required_cols):
        st.warning("Missing required CME columns (linear_speed, width, central_pa).")
        return

    # Filter out invalid placeholders often found in scientific catalogs (e.g. speed < 0)
    df_clean = df[df["linear_speed"] > 0].copy()

    fig = px.scatter(
        df_clean,
        x=df_clean.index,
        y="linear_speed",
        size="width",
        color="linear_speed",
        title="CME Events: Speed & Width over Time",
        labels={
            "linear_speed": "Speed (km/s)",
            "timestamp": "Event Time",
            "width": "Angular Width",
        },
        color_continuous_scale=px.colors.sequential.Plasma,
        hover_data=["central_pa", "remarks"],
    )

    st.plotly_chart(fig, use_container_width=True)

    with st.expander("View Speed Distribution"):
        fig_hist = px.histogram(df_clean, x="linear_speed", nbins=30, title="Distribution of CME Speeds")
        st.plotly_chart(fig_hist, use_container_width=True)
