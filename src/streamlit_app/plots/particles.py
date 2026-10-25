import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def plot_particle_flux(df: pd.DataFrame, collection_name: str, key_suffix: str = "") -> None:
    """Visualizes GOES Particle Flux (Protons & Electrons).

    This plot highlights specific channels critical for space weather (P10 and E2.0)
    and overlays NOAA Solar Radiation Storm scales to provide context for the flux values.

    Args:
        df: The dataframe containing particle flux data.
        collection_name: Name of the collection for display.
        key_suffix: Unique suffix for Streamlit widgets to avoid collisions.
    """
    st.subheader(f"Solar Particle Flux Analysis: {collection_name}")

    # Define key channels: P10 (>10 MeV) and E2_0 (>2.0 MeV)
    proton_col = "P10"
    electron_col = "E2_0"

    cols_of_interest = [proton_col, electron_col]
    available_cols = [c for c in cols_of_interest if c in df.columns]

    if not available_cols:
        st.warning(f"Expected columns {cols_of_interest} not found. Cannot create specialized Particle plot.")
        return

    df_plot = df.copy()

    # Clean data: Mask values <= 0 as log scale requires positive numbers
    for col in available_cols:
        df_plot[col] = pd.to_numeric(df_plot[col], errors="coerce")
        df_plot[col] = df_plot[col].mask(df_plot[col] <= 0)

    col_controls, col_ma = st.columns([1, 1])

    with col_controls:
        resample_map = {
            "Original (5-min)": None,
            "Hourly Average": "h",
            "Daily Average": "D",
            "Weekly Average": "W",
        }
        selected_resample = st.selectbox(
            "Time Resolution",
            options=list(resample_map.keys()),
            index=2,
            key=f"part_resample_{key_suffix}",
        )

    # Apply resampling for performance and trend visibility
    freq = resample_map[selected_resample]
    if freq:
        with st.spinner(f"Resampling data to {selected_resample}..."):
            df_plot = df_plot.select_dtypes(include=[np.number]).resample(freq).mean()

    with col_ma:
        ma_window = st.slider(
            "Smoothing Window (Samples)",
            min_value=1,
            max_value=100,
            value=30,
            key=f"part_ma_{key_suffix}",
        )

    if df_plot[available_cols].dropna(how="all").empty:
        st.warning("No valid numeric data found in the selected range.")
        return

    fig = go.Figure()

    # Plot Protons (P10)
    if proton_col in df_plot.columns:
        y_data = df_plot[proton_col]
        if ma_window > 1:
            y_data = y_data.rolling(window=ma_window, min_periods=1).mean()

        fig.add_trace(
            go.Scatter(
                x=df_plot.index,
                y=y_data,
                mode="lines",
                name="Protons >10 MeV (P10)",
                line=dict(color="#FF4B4B", width=1.5),
                hovertemplate="%{y:.2e} pfu<br>(Protons/cm²-s-sr)",
            )
        )

        # Add NOAA S-Scale Backgrounds (S1 to S5)
        fig.add_hrect(
            y0=1e1,
            y1=1e2,
            fillcolor="rgba(255, 255, 0, 0.1)",
            layer="below",
            line_width=0,
            annotation_text="S1 (Minor)",
            annotation_position="top left",
            annotation_font_color="orange",
        )
        fig.add_hrect(
            y0=1e2,
            y1=1e3,
            fillcolor="rgba(255, 165, 0, 0.15)",
            layer="below",
            line_width=0,
            annotation_text="S2 (Moderate)",
            annotation_position="top left",
            annotation_font_color="darkorange",
        )
        fig.add_hrect(
            y0=1e3,
            y1=1e4,
            fillcolor="rgba(255, 69, 0, 0.2)",
            layer="below",
            line_width=0,
            annotation_text="S3 (Strong)",
            annotation_position="top left",
            annotation_font_color="red",
        )
        fig.add_hrect(
            y0=1e4,
            y1=1e6,
            fillcolor="rgba(139, 0, 0, 0.25)",
            layer="below",
            line_width=0,
            annotation_text="S4/S5 (Severe/Extreme)",
            annotation_position="top left",
            annotation_font_color="darkred",
        )

    # Plot Electrons (E2_0)
    if electron_col in df_plot.columns:
        y_data = df_plot[electron_col]
        if ma_window > 1:
            y_data = y_data.rolling(window=ma_window, min_periods=1).mean()

        fig.add_trace(
            go.Scatter(
                x=df_plot.index,
                y=y_data,
                mode="lines",
                name="Electrons >2.0 MeV (E2_0)",
                line=dict(color="#0068C9", width=1, dash="dot"),
                hovertemplate="%{y:.2e} pfu<br>(Electrons/cm²-s-sr)",
            )
        )

        # Electron Alert Threshold
        fig.add_hline(
            y=1000,
            line_dash="dash",
            line_color="blue",
            opacity=0.5,
            annotation_text="Electron Alert Threshold",
            annotation_position="bottom right",
        )

    fig.update_layout(
        title=f"GOES Particle Flux ({selected_resample})",
        yaxis_title="Flux (pfu)",
        yaxis_type="log",
        xaxis_title="UTC Time",
        hovermode="x unified",
        height=500,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(range=[-1, 5]),
    )

    st.plotly_chart(fig, width="stretch")

    with st.expander("Definition of Particle Channels & Storm Scales", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(
                """
            **Channels**
            * **Protons >10 MeV (P10):** The primary indicator for Solar Radiation Storms.
            * **Electrons >2.0 MeV (E2_0):** High fluxes can cause deep dielectric charging.
            """
            )
        with col2:
            st.markdown(
                """
            **NOAA S-Scale (Solar Radiation Storms)**
            Based on >10 MeV Proton Flux:
            * **S1 ($10^1$):** Minor.
            * **S2 ($10^2$):** Moderate.
            * **S3 ($10^3$):** Strong.
            """
            )
