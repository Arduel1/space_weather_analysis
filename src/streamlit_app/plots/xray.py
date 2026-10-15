from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots


def plot_xray_flux(
    df: pd.DataFrame,
    collection_name: str,
    key_suffix: str = "",
    kp_df: Optional[pd.DataFrame] = None,
) -> None:
    """
    Visualizes GOES X-Ray flux data with classification backgrounds.

    The plot displays both Long and Short wave channels and includes background
    zones corresponding to the C, M, and X flare classifications. If Kp index
    data is provided, it is overlaid on a secondary axis.

    Args:
        df: The dataframe containing X-ray flux data.
        collection_name: Name of the collection for display.
        key_suffix: Unique suffix for Streamlit widgets to avoid collisions.
        kp_df: Optional dataframe containing Kp index data.
    """
    st.subheader(f"Solar X-Ray Flux Analysis: {collection_name}")

    long_col = "Long_Wave"
    short_col = "Short_Wave"

    if long_col not in df.columns:
        st.warning(f"Expected column '{long_col}' not found. Cannot create specialized X-ray plot.")
        return

    df_plot = df.copy()

    # Mask values equal to or less than zero since log scale does not support them
    if long_col in df_plot.columns:
        df_plot[long_col] = df_plot[long_col].mask(df_plot[long_col] <= 0)
    if short_col in df_plot.columns:
        df_plot[short_col] = df_plot[short_col].mask(df_plot[short_col] <= 0)

    col_controls, col_ma = st.columns([1, 1])

    with col_controls:
        resample_map = {
            "Original (1-min)": None,
            "Hourly Average": "h",
            "Daily Average": "D",
            "Weekly Average": "W",
        }
        selected_resample = st.selectbox(
            "Time Resolution (Aggregation)",
            options=list(resample_map.keys()),
            index=2,
            key=f"xray_resample_{key_suffix}",
        )

    # Apply resampling
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
            help="Calculates a rolling mean to smooth out noise.",
            key=f"xray_ma_{key_suffix}",
        )

    # Calculate Moving Average if requested
    if ma_window > 1:
        df_plot[f"{long_col}_MA"] = df_plot[long_col].rolling(window=ma_window, min_periods=1).mean()

    if df_plot[[long_col, short_col]].dropna(how="all").empty:
        st.warning("No valid numeric data found in the selected range.")
        return

    # Create subplots with secondary y-axis spec for Kp index
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Add Kp Index Trace as background bars if data is available
    if kp_df is not None and not kp_df.empty:
        # Align Kp data to the plot range
        kp_subset = kp_df.loc[df_plot.index.min() : df_plot.index.max()]

        if not kp_subset.empty:
            # Color coding for Kp strength
            # Green for low activity, Yellow for moderate, Red for strong
            colors = np.where(
                kp_subset["Kp"] >= 7,
                "rgba(255, 0, 0, 0.5)",
                np.where(kp_subset["Kp"] >= 5, "rgba(255, 255, 0, 0.5)", "rgba(128, 128, 128, 0.3)"),
            )

            fig.add_trace(
                go.Bar(
                    x=kp_subset.index,
                    y=kp_subset["Kp"],
                    name="Kp Index",
                    marker_color=colors,
                    opacity=0.4,
                    # Approximate width for three hour bars in milliseconds
                    width=10800000 * 0.9,
                ),
                secondary_y=True,
            )

    # Add Long Wave as Primary Trace
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
        ),
        secondary_y=False,
    )

    # Add Moving Average Trace
    if ma_window > 1:
        fig.add_trace(
            go.Scatter(
                x=df_plot.index,
                y=df_plot[f"{long_col}_MA"],
                mode="lines",
                name=f"Moving Avg ({ma_window})",
                line=dict(color="#FF4B4B", width=2),
                hovertemplate="%{y:.2e} W/m²",
            ),
            secondary_y=False,
        )

    # Add Short Wave as Secondary Trace
    fig.add_trace(
        go.Scatter(
            x=df_plot.index,
            y=df_plot[short_col],
            mode="lines",
            name="Short Wave (0.5-4 Å)",
            line=dict(color="#0068C9", width=1),
            visible="legendonly",
            hovertemplate="%{y:.2e} W/m²",
        ),
        secondary_y=False,
    )

    # Add Flare Class Background Zones via shapes
    fig.add_hrect(
        y0=1e-4,
        y1=1e-2,
        fillcolor="rgba(255, 0, 0, 0.1)",
        layer="below",
        line_width=0,
        annotation_text="X-Class",
        annotation_position="top left",
        annotation_font_color="red",
        secondary_y=False,
    )
    fig.add_hrect(
        y0=1e-5,
        y1=1e-4,
        fillcolor="rgba(255, 165, 0, 0.1)",
        layer="below",
        line_width=0,
        annotation_text="M-Class",
        annotation_position="top left",
        annotation_font_color="orange",
        secondary_y=False,
    )
    fig.add_hrect(
        y0=1e-6,
        y1=1e-5,
        fillcolor="rgba(0, 255, 0, 0.05)",
        layer="below",
        line_width=0,
        annotation_text="C-Class",
        annotation_position="top left",
        annotation_font_color="green",
        secondary_y=False,
    )

    fig.update_layout(
        title=f"GOES X-Ray Flux ({selected_resample})",
        xaxis_title="UTC Time",
        hovermode="x unified",
        height=500,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    # Configure Axes
    fig.update_yaxes(
        title_text="Flux (Watts/m²)",
        type="log",
        range=[-9, -2],
        secondary_y=False,
    )
    fig.update_yaxes(
        title_text="Kp Index (0-9)",
        range=[0, 9.5],
        showgrid=False,
        secondary_y=True,
    )

    st.plotly_chart(fig, width="stretch")

    with st.expander("Definition of Wavelengths & Flare Classes", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(
                """
            **Wavelengths**
            * **Long Wave (1.0–8.0 Å):** Standard classification channel.
            * **Short Wave (0.5–4.0 Å):** Higher-energy X-rays.
            """
            )
        with col2:
            st.markdown(
                """
            **Solar Flare Classification**
            * **X-Class (> $10^{-4}$):** Major events.
            * **M-Class ($10^{-5}$ – $10^{-4}$):** Medium-sized events.
            * **C-Class ($10^{-6}$ – $10^{-5}$):** Small events.
            """
            )
