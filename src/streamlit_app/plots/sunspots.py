from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def _calculate_cycle_peaks(num_cycles: int = 25) -> list[datetime]:
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
    """
    st.subheader(f"☀️ Sunspot Analysis: {collection_name}")

    if "sunspot_number" not in df.columns:
        st.warning("Column 'sunspot_number' not found. Cannot create specialized plot.")
        return

    # Controls for smoothing and filtering
    col_controls1, col_controls2 = st.columns(2)
    with col_controls1:
        # Default to 365 days to visualize the yearly trend/11-year cycle clearly
        window = st.slider("Smoothing Window (Days)", min_value=1, max_value=5000, value=365)

    with col_controls2:
        # Checkbox to optionally filter out zero values which might indicate missing observations or quiet sun
        remove_zeros = st.checkbox("Hide Zero Values (0)", value=False)

    # Calculate Rolling Average on the full dataframe to preserve time index continuity
    df["rolling_mean"] = df["sunspot_number"].rolling(window=window, min_periods=1).mean()

    # Create a cleaner subset for raw data plotting (removing NaNs)
    df_raw = df.dropna(subset=["sunspot_number"])

    # Filter out zero values if the checkbox is selected
    if remove_zeros:
        df_raw = df_raw[df_raw["sunspot_number"] > 0]

    # Plotting with Plotly Graph Objects
    fig = go.Figure()

    # Raw Data (Background)
    # Orange as requested, with opacity to allow overlapping points to be seen
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
    # Bright Red to contrast well with Orange background points
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

    # Cycle Markers (Vertical Lines)
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
        height=600,
    )

    st.plotly_chart(fig, use_container_width=True)

    # Statistics section
    col1, col2, col3 = st.columns(3)
    if not df_raw.empty:
        col1.metric("Max Sunspot Number", int(df_raw["sunspot_number"].max()))
        col2.metric("Average Sunspot Number", f"{df_raw['sunspot_number'].mean():.2f}")
        col3.metric("Total Records", len(df_raw))
