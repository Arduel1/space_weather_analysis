import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def plot_xray_flux(df: pd.DataFrame, collection_name: str, key_suffix: str = "") -> None:
    """
    Visualizes GOES X-Ray flux data with dynamic resampling, moving average,
    and solar flare classification zones.

    Args:
        df: The dataframe containing X-ray flux data.
        collection_name: Name of the collection for display.
        key_suffix: Unique suffix for Streamlit widgets to avoid collisions when
                    reusing this plot in multiple views.
    """
    st.subheader(f"Solar X-Ray Flux Analysis: {collection_name}")

    # --- 1. Data Preparation & Cleaning ---
    long_col = "Long_Wave"
    short_col = "Short_Wave"

    if long_col not in df.columns:
        st.warning(f"Expected column '{long_col}' not found. Cannot create specialized X-ray plot.")
        return

    # Create a working copy
    df_plot = df.copy()

    # Clean data: Replace <= 0 with NaN (log scale doesn't allow 0)
    if long_col in df_plot.columns:
        df_plot[long_col] = df_plot[long_col].mask(df_plot[long_col] <= 0)
    if short_col in df_plot.columns:
        df_plot[short_col] = df_plot[short_col].mask(df_plot[short_col] <= 0)

    # --- 2. User Controls (Resampling & Moving Average) ---
    col_controls, col_ma = st.columns([1, 1])

    with col_controls:
        resample_map = {"Original (1-min)": None, "Hourly Average": "h", "Daily Average": "D", "Weekly Average": "W"}
        # Unique key for widget
        selected_resample = st.selectbox(
            "Time Resolution (Aggregation)",
            options=list(resample_map.keys()),
            index=2,  # Default to Hourly
            key=f"xray_resample_{key_suffix}",
        )

    # Apply Resampling
    freq = resample_map[selected_resample]
    if freq:
        with st.spinner(f"Resampling data to {selected_resample}..."):
            df_plot = df_plot.select_dtypes(include=[np.number]).resample(freq).mean()

    # Moving Average Control
    with col_ma:
        ma_window = st.slider(
            "Smoothing Window (Samples)",
            min_value=1,
            max_value=100,
            value=30,
            help="Calculates a rolling mean to smooth out noise.",
            key=f"xray_ma_{key_suffix}",
        )

    # Apply Moving Average if window > 1
    if ma_window > 1:
        df_plot[f"{long_col}_MA"] = df_plot[long_col].rolling(window=ma_window, min_periods=1).mean()

    # Check if data is empty after cleaning
    if df_plot[[long_col, short_col]].dropna(how="all").empty:
        st.warning("No valid numeric data found in the selected range.")
        return

    # --- 3. Visualization ---

    fig = go.Figure()

    # Add Long Wave (Primary) - Red (dimmed if MA is active)
    primary_opacity = 0.3 if ma_window > 1 else 1.0
    fig.add_trace(
        go.Scatter(
            x=df_plot.index,
            y=df_plot[long_col],
            mode="lines",
            name="Long Wave (1-8 Å)",
            line=dict(color="green"),
            opacity=primary_opacity,
            hovertemplate="%{y:.2e} W/m²",
        )
    )

    # Add Moving Average Trace (if active)
    if ma_window > 1:
        fig.add_trace(
            go.Scatter(
                x=df_plot.index,
                y=df_plot[f"{long_col}_MA"],
                mode="lines",
                name=f"Moving Avg ({ma_window})",
                line=dict(color="#FF4B4B", width=2),  # Dark Red
                hovertemplate="%{y:.2e} W/m²",
            )
        )

    # Add Short Wave (Secondary) - Blue
    fig.add_trace(
        go.Scatter(
            x=df_plot.index,
            y=df_plot[short_col],
            mode="lines",
            name="Short Wave (0.5-4 Å)",
            line=dict(color="#0068C9", width=1),
            visible="legendonly",
            hovertemplate="%{y:.2e} W/m²",
        )
    )

    # Add Flare Class Background Zones (Shapes)
    # X-Class
    fig.add_hrect(
        y0=1e-4,
        y1=1e-2,
        fillcolor="rgba(255, 0, 0, 0.1)",
        layer="below",
        line_width=0,
        annotation_text="X-Class",
        annotation_position="top left",
        annotation_font_color="red",
    )
    # M-Class
    fig.add_hrect(
        y0=1e-5,
        y1=1e-4,
        fillcolor="rgba(255, 165, 0, 0.1)",
        layer="below",
        line_width=0,
        annotation_text="M-Class",
        annotation_position="top left",
        annotation_font_color="orange",
    )
    # C-Class
    fig.add_hrect(
        y0=1e-6,
        y1=1e-5,
        fillcolor="rgba(0, 255, 0, 0.05)",
        layer="below",
        line_width=0,
        annotation_text="C-Class",
        annotation_position="top left",
        annotation_font_color="green",
    )

    fig.update_layout(
        title=f"GOES X-Ray Flux ({selected_resample})",
        yaxis_title="Flux (Watts/m²)",
        yaxis_type="log",
        xaxis_title="UTC Time",
        hovermode="x unified",
        height=500,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(range=[-9, -2]),
    )

    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Definition of Wavelengths & Flare Classes", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("""
            **Wavelengths**
            * **Long Wave (1.0–8.0 Å):** The standard channel used by NOAA to classify solar flares.
            * **Short Wave (0.5–4.0 Å):** Higher-energy X-rays.
            """)
        with col2:
            st.markdown("""
            **Solar Flare Classification**
            * **X-Class (> $10^{-4}$):** Major events (Radio Blackouts).
            * **M-Class ($10^{-5}$ – $10^{-4}$):** Medium-sized events.
            * **C-Class ($10^{-6}$ – $10^{-5}$):** Small events.
            """)
