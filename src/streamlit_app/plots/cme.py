import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from streamlit_app.util.stats import aggregate_categorical_time_distribution


def plot_cmes(df: pd.DataFrame, collection_name: str) -> None:
    st.subheader(f"CME Impact Analysis: {collection_name}")

    # 1. Data Prep
    df_plot = df.copy()

    # Cleaning
    cols_to_clean = ["linear_speed", "width", "kinetic_energy"]
    for col in cols_to_clean:
        if col in df_plot.columns:
            df_plot[col] = pd.to_numeric(df_plot[col], errors="coerce")
            df_plot.loc[df_plot[col] <= 0, col] = np.nan

    # 2. Check Data Availability
    has_energy = "kinetic_energy" in df_plot.columns
    has_halo = "is_halo" in df_plot.columns

    # --- TABS ---
    tab1, tab2, tab3 = st.tabs(["Timeline & Impact", "Energy Distribution", "Morphology"])

    # --- TAB 1: IMPACT TIMELINE (KORRIGIERT) ---
    with tab1:
        st.caption("Temporal view of CME events by Energy and Type. High-Energy Halo CMEs (Top Red) are the most critical.")

        if has_energy:
            # Filter valid energy data
            df_timeline = df_plot.dropna(subset=["kinetic_energy"]).copy()

            if not df_timeline.empty:
                # Log-Transformation für die Y-Achse
                df_timeline["log_energy"] = np.log10(df_timeline["kinetic_energy"])
                df_timeline["energy_readable"] = df_timeline["kinetic_energy"].apply(lambda x: f"{x:.1e}")

                # Halo Label für Legende schön machen
                if has_halo:
                    df_timeline["Type"] = df_timeline["is_halo"].map({True: "Halo (Earth-Directed)", False: "Non-Halo"})
                else:
                    df_timeline["Type"] = "Unknown"

                # Plot
                fig_time = px.scatter(
                    df_timeline,
                    x=df_timeline.index,  # KORREKTUR: Index statt "timestamp" String
                    y="log_energy",
                    color="Type",
                    size="width",
                    size_max=40,
                    title="CME Impact Timeline: Energy vs. Time",
                    labels={
                        "timestamp": "Year",  # Plotly nutzt den Index-Namen automatisch
                        "log_energy": "Kinetic Energy (Log10 erg)",
                        "width": "Angular Width (Size)",
                        "Type": "CME Morphology",
                    },
                    color_discrete_map={"Halo (Earth-Directed)": "#FF4B4B", "Non-Halo": "rgba(0, 104, 201, 0.5)"},
                    hover_data=["linear_speed", "energy_readable", "remarks"],
                )

                # --- REFERENZLINIEN (KONTEXT) ---
                # Carrington Limit (Gefahrenzone)
                fig_time.add_hline(
                    y=32.0, line_dash="dot", line_color="red", annotation_text="Extreme Danger (Carrington Class)", annotation_position="top left"
                )

                # Durchschnitt (Normalität)
                mean_e = df_timeline["log_energy"].mean()
                fig_time.add_hline(y=mean_e, line_dash="dash", line_color="gray", annotation_text=f"Average Event (10^{mean_e:.1f} erg)")

                # Layout Anpassung
                fig_time.update_layout(
                    yaxis=dict(
                        tickmode="array",
                        tickvals=list(range(26, 34)),
                        ticktext=[f"10^{i}" for i in range(26, 34)],  # Schöne 10^30 Labels
                    ),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                )

                st.plotly_chart(fig_time, use_container_width=True)

                st.info("""
                **Interpretation:**
                * **Clusters:** Notice how high-energy events (higher up) cluster around solar maximum years (2000-2002, 2012-2014).
                * **The Danger Zone:** Red bubbles near the top of the chart represent the most dangerous events: Massive energy AND directed towards Earth (Halo).
                """)

            else:
                st.warning("No valid energy data for timeline.")
        else:
            st.warning("Kinetic Energy missing.")

    # --- TAB 2: DISTRIBUTION ---
    with tab2:
        st.caption("Statistical distribution of energy levels (How rare are extreme events?).")
        if has_energy:
            df_energy = df_plot.dropna(subset=["kinetic_energy"]).copy()
            if not df_energy.empty:
                df_energy["log_energy"] = np.log10(df_energy["kinetic_energy"])
                df_energy["energy_str"] = df_energy["kinetic_energy"].apply(lambda x: f"{x:.2e}")

                fig_hist = px.histogram(
                    df_energy,
                    x="log_energy",
                    nbins=25,
                    log_y=True,
                    title="Energy Distribution (Power Law)",
                    labels={"log_energy": "Kinetic Energy (erg)"},
                    color_discrete_sequence=["#FF4B4B"],
                )

                # Add context lines
                fig_hist.add_vline(x=32.0, line_dash="dot", line_color="red", annotation_text="Carrington Event")

                fig_hist.update_layout(xaxis=dict(tickvals=list(range(26, 34)), ticktext=[f"10^{i}" for i in range(26, 34)]), bargap=0.1)
                fig_hist.update_traces(hovertemplate="<b>Count:</b> %{y}<br><b>Energy:</b> approx. %{customdata[0]} erg<extra></extra>")
                st.plotly_chart(fig_hist, use_container_width=True)

    # --- TAB 3: MORPHOLOGY ---
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
                    st.plotly_chart(fig_bar, use_container_width=True)
