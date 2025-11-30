import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from streamlit_app.util.solar_cycles import calculate_cycle_boundaries


def plot_solar_cycle_correlation(cme_df: pd.DataFrame, sunspot_df: pd.DataFrame) -> None:
    """
    Visualizes the correlation between the solar cycle (sunspot number) and CME events.

    Combines a line chart for sunspots (background) with a scatter plot for CMEs (foreground)
    on a dual-axis chart to highlight relationships between cycle phases and event intensity.

    Args:
        cme_df: Dataframe containing CME data.
        sunspot_df: Dataframe containing sunspot data.
    """
    st.subheader("Solar Cycle vs. CME Events")
    st.caption("This visualization overlays individual CME events (bubbles) on the solar cycle (sunspot number).")

    # Prepare CME data
    cme_plot = cme_df.copy()

    for col in ["kinetic_energy", "width"]:
        if col in cme_plot.columns:
            cme_plot[col] = pd.to_numeric(cme_plot[col], errors="coerce")
            cme_plot.loc[cme_plot[col] <= 0, col] = np.nan

    cme_plot = cme_plot.dropna(subset=["kinetic_energy"])

    if cme_plot.empty:
        st.warning("No valid CME energy data found for correlation.")
        return

    cme_plot["log_energy"] = np.log10(cme_plot["kinetic_energy"])
    cme_plot["energy_readable"] = cme_plot["kinetic_energy"].apply(lambda x: f"{x:.1e}")

    if "is_halo" in cme_plot.columns:
        cme_plot["Type"] = cme_plot["is_halo"].map({True: "Halo (Earth-Directed)", False: "Non-Halo"})
    else:
        cme_plot["Type"] = "Unknown"

    # Prepare Sunspot data (Background)
    sunspot_plot = sunspot_df.copy()
    if "sunspot_number" in sunspot_plot.columns:
        # Use a rolling average to visualize the general cycle shape rather than daily noise
        sunspot_plot["rolling_mean"] = sunspot_plot["sunspot_number"].rolling(window=365, min_periods=1).mean()
    else:
        st.warning("Sunspot data missing 'sunspot_number'. Cannot plot background.")
        return

    # Create Dual-Axis Plot
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Sunspots (Background) - Right Y-Axis
    fig.add_trace(
        go.Scatter(
            x=sunspot_plot.index,
            y=sunspot_plot["rolling_mean"],
            mode="lines",
            name="Sunspot Number (365d Avg)",
            line=dict(width=2, color="#FF4B4B"),
            fill="tozeroy",
            fillcolor="rgba(255, 165, 0, 0.25)",
            hoverinfo="x+y",
        ),
        secondary_y=True,
    )

    # CMEs (Foreground) - Left Y-Axis
    colors = {"Halo (Earth-Directed)": "#FF4B4B", "Non-Halo": "rgba(0, 104, 201, 0.5)", "Unknown": "grey"}

    # max() on empty sequence safety check not needed here due to earlier empty check
    size_ref = 2.0 * cme_plot["width"].max() / (30.0**2)

    for cme_type, color in colors.items():
        subset = cme_plot[cme_plot["Type"] == cme_type]
        if subset.empty:
            continue

        fig.add_trace(
            go.Scatter(
                x=subset.index,
                y=subset["log_energy"],
                mode="markers",
                name=f"CME: {cme_type}",
                marker=dict(size=subset["width"], sizemode="area", sizeref=size_ref, sizemin=3, color=color, line=dict(width=0.5, color="white")),
                customdata=np.stack((subset["linear_speed"], subset["energy_readable"]), axis=-1),
                hovertemplate="<b>Date:</b> %{x}<br><b>Energy:</b> 10^%{y:.1f} erg<br><b>Speed:</b> %{customdata[0]} km/s<br>"
                "<b>E_raw:</b> %{customdata[1]}<extra></extra>",
            ),
            secondary_y=False,
        )

    # Layout Configuration
    cycle_boundaries = calculate_cycle_boundaries(num_cycles=30)

    # Calculate dynamic axis range for exponents
    min_log = np.floor(cme_plot["log_energy"].min())
    max_log = np.ceil(cme_plot["log_energy"].max())
    tick_vals = list(range(int(min_log), int(max_log) + 2))
    tick_text = [f"10^{i}" for i in tick_vals]

    fig.update_layout(
        title="Correlation Analysis: CME Energy vs. Solar Cycle",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=600,
        margin=dict(l=20, r=20, t=60, b=20),
    )

    # Add cycle boundary lines within the data range
    data_min_date = sunspot_plot.index.min()
    data_max_date = sunspot_plot.index.max()

    for boundary in cycle_boundaries:
        if data_min_date <= boundary <= data_max_date:
            # Convert datetime to ms timestamp for Plotly shape compatibility
            boundary_ms = pd.Timestamp(boundary).timestamp() * 1000

            fig.add_vline(
                x=boundary_ms,
                line_width=2,
                line_dash="dash",
                line_color="rgba(128, 128, 128, 0.5)",
                annotation_text="Cycle",
                annotation_position="bottom right",
            )

    # Y-Axis 1 (Left): CME Energy
    fig.update_yaxes(
        title_text="CME Kinetic Energy (erg)",
        tickmode="array",
        tickvals=tick_vals,
        ticktext=tick_text,
        secondary_y=False,
        showgrid=True,
        gridcolor="rgba(128,128,128,0.2)",
    )

    # Y-Axis 2 (Right): Sunspots
    # Force range to start at 0 to align visual baseline
    max_sunspots = sunspot_plot["rolling_mean"].max()
    fig.update_yaxes(title_text="Sunspot Number (Smoothed)", secondary_y=True, showgrid=False, range=[0, max_sunspots], color="#FF4B4B")

    st.plotly_chart(fig, width="stretch")

    with st.expander("**Chart Explanation:**", expanded=False):
        st.markdown("""
        * **Background (Orange Area/Red Line):** The **Solar Cycle** (Smoothed Sunspot Number). The dashed lines mark the approximate cycle minima.
        * **Foreground (Bubbles):** **CME Events**. The height represents energy, the size represents width.
        """)  # noqa: W293
