import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from streamlit_app.util.stats import aggregate_categorical_time_distribution


def plot_cmes(df: pd.DataFrame, collection_name: str) -> None:
    """
    Visualizes Coronal Mass Ejection (CME) data, focusing on timeline impact,
    energy distribution, and morphology.

    Args:
        df: The dataframe containing CME data.
        collection_name: Name of the collection for display purposes.
    """
    st.subheader(f"CME Impact Analysis: {collection_name}")

    df_plot = df.copy()

    # Clean numeric columns and coerce errors
    cols_to_clean = ["linear_speed", "width", "kinetic_energy"]
    for col in cols_to_clean:
        if col in df_plot.columns:
            df_plot[col] = pd.to_numeric(df_plot[col], errors="coerce")
            df_plot.loc[df_plot[col] <= 0, col] = np.nan

    has_energy = "kinetic_energy" in df_plot.columns
    has_halo = "is_halo" in df_plot.columns

    tab1, tab2, tab3 = st.tabs(["Timeline & Impact", "Energy Distribution", "Morphology"])

    # Calculate dynamic log-range for consistent axis scaling across tabs
    tick_vals = []
    tick_text = []

    if has_energy:
        valid_energy = df_plot["kinetic_energy"].dropna()
        if not valid_energy.empty:
            min_log = np.floor(np.log10(valid_energy.min()))
            max_log = np.ceil(np.log10(valid_energy.max()))

            # Generate integer ticks for log axis (e.g., 10^28)
            tick_vals = list(range(int(min_log), int(max_log) + 2))
            tick_text = [f"10^{i}" for i in tick_vals]

    # Timeline Plot
    with tab1:
        st.caption("Temporal view of CME events by Energy and Type. High-Energy Halo CMEs (Top Red) are the most critical.")

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
                    color_discrete_map={"Halo (Earth-Directed)": "#FF4B4B", "Non-Halo": "rgba(0, 104, 201, 0.5)"},
                    hover_data=["linear_speed", "energy_readable", "remarks"],
                )

                # Reference Line: Carrington Limit
                fig_time.add_hline(
                    y=32.0, line_dash="dot", line_color="red", annotation_text="Extreme Danger (Carrington Class)", annotation_position="top left"
                )

                # Reference Line: Average Energy
                mean_e = df_timeline["log_energy"].mean()
                fig_time.add_hline(y=mean_e, line_dash="dash", line_color="gray", annotation_text=f"Average Event (10^{mean_e:.1f} erg)")

                fig_time.update_layout(
                    yaxis=dict(
                        tickmode="array",
                        tickvals=tick_vals,
                        ticktext=tick_text,
                    ),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )

                st.plotly_chart(fig_time, width="stretch")

                st.info("""
                **Interpretation:**
                * **Clusters:** Notice how high-energy events (higher up) cluster around solar maximum years.
                * **The Danger Zone:** Red bubbles near the top of the chart represent the most dangerous events: Massive energy AND directed towards Earth (Halo).
                """)  # noqa: E501

            else:
                st.warning("No valid energy data for timeline.")
        else:
            st.warning("Kinetic Energy missing.")

    # Distribution Plot
    with tab2:
        st.caption("Statistical distribution of energy levels (How rare are extreme events?).")
        if has_energy:
            df_energy = df_plot.dropna(subset=["kinetic_energy"]).copy()
            if not df_energy.empty:
                df_energy["log_energy"] = np.log10(df_energy["kinetic_energy"])

                # Dynamic bin count using square-root rule
                n_bins = int(np.sqrt(len(df_energy)))
                n_bins = max(10, min(n_bins, 100))

                fig_hist = px.histogram(
                    df_energy,
                    x="log_energy",
                    nbins=n_bins,
                    log_y=True,
                    title=f"Energy Distribution (Power Law) - {n_bins} Bins",
                    labels={"log_energy": "Kinetic Energy (erg)"},
                    color_discrete_sequence=["#FF4B4B"],
                )

                fig_hist.add_vline(x=32.0, line_dash="dot", line_color="red", annotation_text="Carrington Event")

                fig_hist.update_layout(xaxis=dict(tickvals=tick_vals, ticktext=tick_text), bargap=0.1)

                st.plotly_chart(fig_hist, width="stretch")

    # Morphology Plot
    with tab3:
        if has_halo:
            yearly = aggregate_categorical_time_distribution(df_plot, "is_halo")
            if not yearly.empty:
                yearly = yearly.rename(columns={True: "Halo", False: "Non-Halo"})
                cols = [c for c in ["Halo", "Non-Halo"] if c in yearly.columns]
                if cols:
                    fig_bar = px.bar(
                        yearly,
                        x=yearly.index,
                        y=cols,
                        title="CME Counts per Year",
                        color_discrete_map={"Halo": "red", "Non-Halo": "blue"},
                        labels={"value": "Count", "timestamp": "Year", "variable": "Type"},
                    )
                    st.plotly_chart(fig_bar, width="stretch")
