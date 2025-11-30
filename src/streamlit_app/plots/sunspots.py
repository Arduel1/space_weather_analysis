from datetime import datetime
from typing import List

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from streamlit_app.util.fast_fourier_transformation import calculate_periodicity


def _calculate_cycle_peaks(num_cycles: int = 25) -> List[datetime]:
    """
    Calculates theoretical sunspot cycle peaks based on the formula:
    timepoint = 1810.6 + 11.1 * i
    """
    peaks = []
    for i in range(num_cycles):
        timepoint = 1810.6 + 11.1 * i
        year = int(timepoint)
        # Calculate month from the decimal part of the year
        month = int((timepoint - year) * 12) + 1
        # Create a datetime object (using 1st of the month)
        dt = datetime(year, month, 1)
        peaks.append(dt)
    return peaks


def plot_sunspots(df: pd.DataFrame, collection_name: str) -> None:
    """
    Visualizes sunspot data with specialized 11-year cycle markers.
    Includes raw data points (orange) and a prominent smoothing line (red).
    Includes a frequency analysis to statistically prove the cycle length.

    Args:
        df: Dataframe containing sunspot data.
        collection_name: Name of the collection for display.
    """
    st.subheader(f"☀️ Sunspot Analysis: {collection_name}")

    if "sunspot_number" not in df.columns:
        st.warning("Column 'sunspot_number' not found. Cannot create specialized plot.")
        return

    col_controls1, col_controls2 = st.columns(2)
    with col_controls1:
        # Default to 365 days to visualize the yearly trend/11-year cycle clearly
        window = st.slider("Smoothing Window (Days)", min_value=1, max_value=5000, value=365)

    with col_controls2:
        remove_zeros = st.checkbox("Hide Zero Values (0)", value=False)

    # Calculate Rolling Average on the full dataframe to preserve time index continuity
    df["rolling_mean"] = df["sunspot_number"].rolling(window=window, min_periods=1).mean()

    # Create a cleaner subset for raw data plotting (removing NaNs)
    df_raw = df.dropna(subset=["sunspot_number"])

    if remove_zeros:
        df_raw = df_raw[df_raw["sunspot_number"] > 0]

    # --- Plotting Main Time Series ---
    fig = go.Figure()

    # Raw Data (Background)
    fig.add_trace(
        go.Scatter(
            x=df_raw.index,
            y=df_raw["sunspot_number"],
            mode="markers",
            name="Daily Observations",
            marker=dict(
                color="rgba(255, 165, 0, 0.6)",  # Orange with 60% opacity
                size=3,
            ),
        )
    )

    # Smoothed Trend (Foreground)
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["rolling_mean"],
            mode="lines",
            name=f"{window}-Day Moving Average",
            line=dict(
                color="#FF4B4B",  # Streamlit Red
                width=3,
            ),
        )
    )

    # Cycle Markers (Theoretical)
    cycle_peaks = _calculate_cycle_peaks(30)
    shapes = []
    for peak in cycle_peaks:
        # Only add lines that are within the current data view
        if df.index.min() <= peak <= df.index.max():
            shapes.append(dict(type="line", x0=peak, y0=0, x1=peak, y1=1, xref="x", yref="paper", line=dict(color="blue", width=1, dash="dash")))

    fig.update_layout(
        title=f"Sunspot Cycles (Smoothed vs. Daily) - {collection_name}",
        xaxis_title="Date",
        yaxis_title="Sunspot Number",
        hovermode="x unified",
        shapes=shapes,
        showlegend=True,
        height=500,
    )

    st.plotly_chart(fig, use_container_width=True)

    # Statistics section (Metrics)
    col1, col2, col3 = st.columns(3)
    if not df_raw.empty:
        col1.metric("Max Sunspot Number", int(df_raw["sunspot_number"].max()))
        col2.metric("Average Sunspot Number", f"{df_raw['sunspot_number'].mean():.2f}")
        col3.metric("Total Records", len(df_raw))

    # --- Scientific Analysis Section ---
    st.markdown("---")
    st.subheader("Periodicity")

    with st.expander("Cycle Analysis (Fourier Transform)", expanded=True):
        st.caption("""
        This analysis breaks down the sunspot time series into repeating cycles.
        Peaks in the graph below indicate the most dominant repeating patterns (cycles) in the data.
        For sunspots, we expect a strong peak around 11 years.
        """)

        # Perform calculation using the utility
        spectrum_df = calculate_periodicity(
            df["sunspot_number"],
            sample_rate_days=1,
            min_period_years=2.0,  # Ignore short-term noise < 2 years
            max_period_years=25.0,  # Ignore very long trends > 25 years
        )

        if not spectrum_df.empty:
            # Find the strongest cycle
            top_cycle = spectrum_df.iloc[0]
            top_period = top_cycle["Period_Years"]

            col_res_1, col_res_2 = st.columns([1, 3])

            with col_res_1:
                st.metric(label="Dominant Cycle Detected", value=f"{top_period:.2f} Years", delta="Target: ~11.0 Years", delta_color="normal")
                st.info("The calculation uses a Fast Fourier Transform (FFT) on the daily data.")

            with col_res_2:
                # Plot the Power Spectrum
                fig_fft = px.line(
                    spectrum_df,
                    x="Period_Years",
                    y="Power",
                    title="Periodogram: Strength of Cycles",
                    labels={"Period_Years": "Cycle Length (Years)", "Power": "Relative Strength (Power)"},
                )
                # Highlight the 11-year mark
                fig_fft.add_vline(x=11.0, line_width=2, line_dash="dash", line_color="green", annotation_text="Schwabe Cycle (11y)")

                # Make the dominant peak stand out
                fig_fft.add_annotation(x=top_period, y=top_cycle["Power"], text=f"Peak: {top_period:.2f}y", showarrow=True, arrowhead=1)
                st.plotly_chart(fig_fft, use_container_width=True)
        else:
            st.warning("Not enough data to perform a reliable periodicity analysis.")
