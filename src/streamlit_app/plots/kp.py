import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def plot_kp_index(df: pd.DataFrame) -> None:
    """
    Visualizes the Planetary K-index (Kp) with color-coded severity levels.

    The plot uses a bar chart where the color indicates the geomagnetic activity
    category, ranging from quiet (Green) to extreme storm (Dark Red).

    Args:
        df: DataFrame containing the 'Kp' column and a datetime index.
    """
    st.subheader("Planetary K-index (Geomagnetic Activity)")

    if df is None or df.empty or "Kp" not in df.columns:
        st.info("No Kp index data available for this range.")
        return

    # Define color scale based on Kp value (G-Scale approximate mapping)
    # 0-4: Quiet/Unsettled (Green)
    # 5: G1 Minor (Yellow)
    # 6: G2 Moderate (Orange)
    # 7: G3 Strong (Red)
    # 8: G4 Severe (Dark Red)
    # 9: G5 Extreme (Purple/Black equivalent or vivid Red)

    def get_color(kp_value):
        if kp_value < 5:
            return "#2ECC71"  # Green
        elif kp_value < 6:
            return "#F1C40F"  # Yellow
        elif kp_value < 7:
            return "#E67E22"  # Orange
        elif kp_value < 8:
            return "#E74C3C"  # Red
        else:
            return "#8E44AD"  # Purple/Dark Red for extreme

    colors = [get_color(val) for val in df["Kp"]]

    fig = go.Figure()

    # Add Kp Bars
    fig.add_trace(go.Bar(x=df.index, y=df["Kp"], marker_color=colors, name="Kp Index", opacity=0.9))

    # Add Threshold Lines for G-Scales
    # G1 (Kp=5)
    fig.add_hline(y=5, line_dash="dot", line_color="gray", annotation_text="G1 (Minor)", annotation_position="top left")
    # G3 (Kp=7)
    fig.add_hline(y=7, line_dash="dot", line_color="gray", annotation_text="G3 (Strong)", annotation_position="top left")

    fig.update_layout(
        title="Kp Index Status",
        yaxis_title="Kp Index (0-9)",
        xaxis_title="UTC Time",
        yaxis=dict(range=[0, 9.5], tickmode="linear", tick0=0, dtick=1),
        height=300,  # Compact height to fit alongside or below X-ray
        showlegend=False,
        hovermode="x unified",
    )

    st.plotly_chart(fig, width="stretch")

    # Legend for the user
    st.caption("Color Key: Green (Quiet <5), Yellow (G1), Orange (G2), Red (G3), Purple (G4/G5)")
