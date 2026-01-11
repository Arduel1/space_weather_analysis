import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from streamlit_app.util.solar_cycles import calculate_cycle_boundaries


def plot_sunspots(df: pd.DataFrame, collection_name: str) -> None:
    """
    Visualizes sunspot data with specialized 11-year cycle markers.

    This function creates a dual-layer visualization showing raw daily observations
    alongside a smoothed trend line to highlight the solar cycle. It also overlaps
    theoretical cycle boundaries to provide context on the current cycle phase.

    Args:
        df: Dataframe containing sunspot data with a 'sunspot_number' column.
        collection_name: Name of the collection for display.
    """
    st.subheader(f"Sunspot Analysis: {collection_name}")

    if "sunspot_number" not in df.columns:
        st.warning("Column 'sunspot_number' not found. Cannot create specialized plot.")
        return

    col_controls1, col_controls2 = st.columns(2)
    with col_controls1:
        window = st.slider("Smoothing Window (Days)", min_value=1, max_value=5000, value=365)

    with col_controls2:
        remove_zeros = st.checkbox("Hide Zero Values (0)", value=False)

    # Calculate Rolling Average to visualize the trend
    df["rolling_mean"] = df["sunspot_number"].rolling(window=window, min_periods=1).mean()

    # Filter data for the raw scatter plot
    df_raw = df.dropna(subset=["sunspot_number"])

    if remove_zeros:
        df_raw = df_raw[df_raw["sunspot_number"] > 0]

    fig = go.Figure()

    # Add Daily Observations as background markers
    fig.add_trace(
        go.Scatter(
            x=df_raw.index,
            y=df_raw["sunspot_number"],
            mode="markers",
            name="Daily Observations",
            marker=dict(
                color="rgba(255, 165, 0, 0.6)",
                size=3,
            ),
        )
    )

    # Add Smoothed Trend as the primary line
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df["rolling_mean"],
            mode="lines",
            name=f"{window}-Day Moving Average",
            line=dict(
                color="#FF4B4B",
                width=3,
            ),
        )
    )

    # Add Cycle Markers based on theoretical minima
    cycle_boundaries = calculate_cycle_boundaries(num_cycles=30)
    shapes = []
    data_min_date = df.index.min()
    data_max_date = df.index.max()

    for boundary in cycle_boundaries:
        if data_min_date <= boundary <= data_max_date:
            shapes.append(
                dict(
                    type="line",
                    x0=boundary,
                    y0=0,
                    x1=boundary,
                    y1=1,
                    xref="x",
                    yref="paper",
                    line=dict(color="blue", width=1, dash="dash"),
                )
            )

    fig.update_layout(
        title=f"Sunspot Cycles (Smoothed vs. Daily) - {collection_name}",
        xaxis_title="Date",
        yaxis_title="Sunspot Number",
        hovermode="x unified",
        shapes=shapes,
        showlegend=True,
        height=500,
    )

    st.plotly_chart(fig, width="stretch")

    col1, col2, col3 = st.columns(3)
    if not df_raw.empty:
        col1.metric("Max Sunspot Number", int(df_raw["sunspot_number"].max()))
        col2.metric("Average Sunspot Number", f"{df_raw['sunspot_number'].mean():.2f}")
        col3.metric("Total Records", len(df_raw))
