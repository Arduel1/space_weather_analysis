import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def plot_particle_flux(df: pd.DataFrame, collection_name: str, key_suffix: str = "") -> None:
    """
    Visualizes GOES Particle Flux (Protons & Electrons) with dynamic resampling,
    moving average, and NOAA Solar Radiation Storm (S-Scale) zones.

    Args:
        df: The dataframe containing particle flux data.
        collection_name: Name of the collection for display.
        key_suffix: Unique suffix for Streamlit widgets to avoid collisions.
    """
    st.subheader(f"Solar Particle Flux Analysis: {collection_name}")

    # --- 1. Data Preparation ---
    # Key channels for Space Weather impacts
    # P10 (>10 MeV) is the definition for NOAA Solar Radiation Storms
    # E2_0 (>2.0 MeV) is critical for satellite deep dielectric charging
    proton_col = "P10"
    electron_col = "E2_0"

    cols_of_interest = [proton_col, electron_col]
    available_cols = [c for c in cols_of_interest if c in df.columns]

    if not available_cols:
        st.warning(f"Expected columns {cols_of_interest} not found. Cannot create specialized Particle plot.")
        return

    # Create working copy
    df_plot = df.copy()

    # Clean data: Metadata says fill is -100000.0. Log scale needs > 0.
    # We coerce errors and mask invalid values.
    for col in available_cols:
        df_plot[col] = pd.to_numeric(df_plot[col], errors="coerce")
        df_plot[col] = df_plot[col].mask(df_plot[col] <= 0)

    # --- 2. User Controls ---
    col_controls, col_ma = st.columns([1, 1])

    with col_controls:
        resample_map = {"Original (5-min)": None, "Hourly Average": "h", "Daily Average": "D", "Weekly Average": "W"}
        selected_resample = st.selectbox(
            "Time Resolution",
            options=list(resample_map.keys()),
            index=2,  # Default to Hourly for performance
            key=f"part_resample_{key_suffix}",
        )

    # Apply Resampling
    freq = resample_map[selected_resample]
    if freq:
        with st.spinner(f"Resampling data to {selected_resample}..."):
            df_plot = df_plot.select_dtypes(include=[np.number]).resample(freq).mean()

    # Moving Average Control
    with col_ma:
        ma_window = st.slider("Smoothing Window (Samples)", min_value=1, max_value=100, value=30, key=f"part_ma_{key_suffix}")

    # Check if data is empty after cleaning
    if df_plot[available_cols].dropna(how="all").empty:
        st.warning("No valid numeric data found in the selected range.")
        return

    # --- 3. Visualization ---
    fig = go.Figure()

    # Plot Protons (P10) - The Storm Indicator
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

        # --- NOAA S-Scale (Solar Radiation Storms) Backgrounds ---
        # Scale applies to >10 MeV Protons
        # S1: 10^1, S2: 10^2, S3: 10^3, S4: 10^4, S5: 10^5

        # S1 (Minor)
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
        # S2 (Moderate)
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
        # S3 (Strong)
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
        # S4-S5 (Severe/Extreme)
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

        # Electron Alert Threshold (Example: 1000 pfu is often used as a watch level)
        fig.add_hline(
            y=1000, line_dash="dash", line_color="blue", opacity=0.5, annotation_text="Electron Alert Threshold", annotation_position="bottom right"
        )

    # Layout Updates
    fig.update_layout(
        title=f"GOES Particle Flux ({selected_resample})",
        yaxis_title="Flux (pfu)",
        yaxis_type="log",
        xaxis_title="UTC Time",
        hovermode="x unified",
        height=500,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        # Typical range for particle monitors
        yaxis=dict(range=[-1, 5]),
    )

    st.plotly_chart(fig, width="stretch")

    # --- Explanations ---
    with st.expander("Definition of Particle Channels & Storm Scales", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("""
            **Channels**
            * **Protons >10 MeV (P10):** The primary indicator for Solar Radiation Storms. These particles can penetrate satellite shielding and pose biological risks.
            * **Electrons >2.0 MeV (E2_0):** High fluxes can cause deep dielectric charging inside satellites, leading to component damage.
            """)  # noqa: E501
        with col2:
            st.markdown("""
            **NOAA S-Scale (Solar Radiation Storms)**
            Based on >10 MeV Proton Flux:
            * **S1 ($10^1$):** Minor. No biological risk, minor satellite impacts.
            * **S2 ($10^2$):** Moderate. Infrequent satellite single-event upsets.
            * **S3 ($10^3$):** Strong. Radiation hazard for astronauts, satellite noise.
            """)
