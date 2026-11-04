import pandas as pd
import plotly.graph_objects as go

from space_weather_analysis.scales.noaa import G_THRESHOLDS_KP, g_level
from streamlit_app.figures.scale_style import add_scale_thresholds, level_colors

# Width of one Kp bar in milliseconds, 90 percent of the 3-hour Kp interval.
_KP_BAR_WIDTH_MS = 3 * 3600 * 1000 * 0.9


def kp_figure(frame: pd.DataFrame, *, kp_column: str = "Kp", estimate: pd.Series | None = None) -> go.Figure:
    """Builds a bar chart of the 3-hourly Kp index colored by NOAA geomagnetic storm level.

    Kp is the planetary K index of geomagnetic activity, 0 to 9 in steps of one third.

    Args:
        frame: Kp values with a UTC DatetimeIndex.
        kp_column: Column holding the Kp values.
        estimate: Kp values estimated before the official ones are published, with a UTC DatetimeIndex; None draws no estimate.

    Returns:
        One bar trace, with an estimate a line trace above it, and a line at the lowest Kp of each G level.
    """
    kp = frame.loc[~frame.index.duplicated(keep="first"), kp_column]
    fig = go.Figure(
        go.Bar(
            x=kp.index,
            y=kp,
            width=_KP_BAR_WIDTH_MS,
            marker_color=level_colors(g_level(kp)),
            name="Kp",
            hovertemplate="Kp %{y:.2f}<extra></extra>",
        )
    )
    if estimate is not None:
        fig.add_trace(
            go.Scattergl(
                x=estimate.index,
                y=estimate,
                mode="lines",
                name="Estimated Kp",
                line={"color": "#0068C9", "width": 1},
                hovertemplate="Estimated Kp %{y:.2f}<extra></extra>",
            )
        )
    add_scale_thresholds(fig, G_THRESHOLDS_KP, "G")
    fig.update_layout(
        title="Kp index (geomagnetic activity)",
        xaxis_title="UTC time",
        yaxis={"title": "Kp (0-9)", "range": [0, 9.5], "tickmode": "linear", "tick0": 0, "dtick": 1},
        showlegend=estimate is not None,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
        hovermode="x unified",
    )
    return fig
