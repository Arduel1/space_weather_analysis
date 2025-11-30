import pandas as pd
import plotly.express as px
import streamlit as st


def plot_generic(df: pd.DataFrame, collection_name: str) -> None:
    """
    Provides a generic time-series visualization for any dataset with numeric columns.
    """
    st.subheader(f"Generic Time Series: {collection_name}")

    numeric_cols = df.select_dtypes(include=["float", "int"]).columns.tolist()

    if not numeric_cols:
        st.warning("No numeric columns found to plot.")
        return

    selected_vars = st.multiselect(
        "Select Variables",
        options=numeric_cols,
        default=numeric_cols[:2] if len(numeric_cols) >= 2 else numeric_cols[:1],
    )

    if selected_vars:
        # Check if dataset is extremely large and suggest resampling to prevent browser lag
        if len(df) > 50000:
            st.info(f"Dataset is large ({len(df)} rows). Displaying hourly averages for performance.")
            df_plot = df[selected_vars].resample("h").mean()
        else:
            df_plot = df

        fig = px.line(df_plot, x=df_plot.index, y=selected_vars, title=f"{collection_name} - Time Series")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Please select at least one variable to plot.")
