import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st


def plot_cmes(df: pd.DataFrame, collection_name: str) -> None:
    """Visualizes Coronal Mass Ejection (CME) data.

    Renders a timeline scatter plot of kinetic energy (log10) per event, colored
    by Halo vs Non-Halo type and sized by angular width, with reference lines
    for the Carrington class and the average energy. Non-numeric and
    non-positive speed, width and energy values are treated as missing.

    Args:
        df: The dataframe containing CME data.
        collection_name: Name of the collection for display purposes.
    """
    st.subheader(f"CME Impact Analysis: {collection_name}")

    df_plot = df.copy()

    # Coerce numeric columns to ensure valid plotting data
    cols_to_clean = ["linear_speed", "width", "kinetic_energy"]
    for col in cols_to_clean:
        if col in df_plot.columns:
            df_plot[col] = pd.to_numeric(df_plot[col], errors="coerce")
            df_plot.loc[df_plot[col] <= 0, col] = np.nan

    has_energy = "kinetic_energy" in df_plot.columns
    has_halo = "is_halo" in df_plot.columns

    # Prepare ticks for logarithmic axis scaling
    tick_vals = []
    tick_text = []

    if has_energy:
        valid_energy = df_plot["kinetic_energy"].dropna()
        if not valid_energy.empty:
            min_log = np.floor(np.log10(valid_energy.min()))
            max_log = np.ceil(np.log10(valid_energy.max()))

            # Generate ticks at integer powers of ten for readability
            tick_vals = list(range(int(min_log), int(max_log) + 2))
            tick_text = [f"10^{i}" for i in tick_vals]

    # Timeline plot
    st.caption("Temporal view of CME events by Energy and Type. High-Energy Halo CMEs (Red) are typically the most critical.")

    if has_energy:
        df_timeline = df_plot.dropna(subset=["kinetic_energy"]).copy()

        if not df_timeline.empty:
            df_timeline["log_energy"] = np.log10(df_timeline["kinetic_energy"])
            df_timeline["energy_readable"] = df_timeline["kinetic_energy"].apply(lambda x: f"{x:.1e}")

            if has_halo:
                df_timeline["Type"] = df_timeline["is_halo"].map({True: "Halo (Earth-Directed)", False: "Non-Halo"})
            else:
                df_timeline["Type"] = "Unknown"

            fig_time = px.scatter(
                df_timeline,
                x=df_timeline.index,
                y="log_energy",
                color="Type",
                size="width",
                size_max=40,
                title="CME Impact Timeline: Energy vs. Time",
                labels={
                    "timestamp": "Year",
                    "log_energy": "Kinetic Energy (Log10 erg)",
                    "width": "Angular Width (Size)",
                    "Type": "CME Morphology",
                },
                color_discrete_map={
                    "Halo (Earth-Directed)": "#FF4B4B",
                    "Non-Halo": "rgba(0, 104, 201, 0.5)",
                },
                hover_data=["linear_speed", "energy_readable", "remarks"],
            )

            # Add reference line for the Carrington Limit
            fig_time.add_hline(
                y=32.0,
                line_dash="dot",
                line_color="red",
                annotation_text="Extreme Danger (Carrington Class)",
                annotation_position="top left",
            )

            # Add reference line for average energy
            mean_e = df_timeline["log_energy"].mean()
            fig_time.add_hline(
                y=mean_e,
                line_dash="dash",
                line_color="gray",
                annotation_text=f"Average Event (10^{mean_e:.1f} erg)",
            )

            fig_time.update_layout(
                yaxis=dict(
                    tickmode="array",
                    tickvals=tick_vals,
                    ticktext=tick_text,
                ),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )

            st.plotly_chart(fig_time, width="stretch")

            st.info(
                """
            **Interpretation:**
            * **Clusters:** High-energy events tend to cluster around solar maximum years.
            * **Risk:** Red bubbles near the top represent events with massive energy
              and Earth-directed trajectory.
            """
            )

        else:
            st.warning("No valid energy data for timeline.")
    else:
        st.warning("Kinetic Energy missing.")
