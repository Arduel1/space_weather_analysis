from collections.abc import Mapping
from types import MappingProxyType

import pandas as pd
import plotly.graph_objects as go

# Stored CME catalog columns shown in the table, mapped to their display labels.
CME_DISPLAY_COLUMNS: Mapping[str, str] = MappingProxyType(
    {
        "linear_speed": "Speed (km/s)",
        "width": "Angular width (deg)",
        "central_pa": "Central position angle (deg)",
        "is_halo": "Halo",
        "kinetic_energy": "Kinetic energy (erg)",
        "remarks": "Remarks",
    }
)


def sunspot_figure(monthly: pd.DataFrame, smoothed: pd.DataFrame | None = None, *, sunspot_column: str = "sunspot_number") -> go.Figure:
    """Builds a line plot of the SILSO monthly mean sunspot number.

    Args:
        monthly: Monthly mean sunspot numbers with a UTC DatetimeIndex.
        smoothed: 13-month smoothed sunspot numbers with a UTC DatetimeIndex, which show the roughly
            11-year solar cycle; None draws no smoothed line.
        sunspot_column: Column of the sunspot number in both frames.

    Returns:
        The monthly mean trace and, with smoothed numbers, the smoothed trace.
    """
    fig = go.Figure(
        go.Scattergl(
            x=monthly.index,
            y=monthly[sunspot_column],
            mode="lines",
            name="Monthly mean",
            line={"color": "rgba(255, 165, 0, 0.7)", "width": 1},
            hovertemplate="%{y:.1f}",
        )
    )
    if smoothed is not None:
        fig.add_trace(
            go.Scattergl(
                x=smoothed.index,
                y=smoothed[sunspot_column],
                mode="lines",
                name="13-month smoothed",
                line={"color": "#FF4B4B", "width": 3},
                hovertemplate="%{y:.1f}",
            )
        )
    fig.update_layout(
        title="Sunspot number",
        xaxis_title="Date",
        yaxis_title="Sunspot number",
        hovermode="x unified",
    )
    return fig


def cme_table_frame(
    frame: pd.DataFrame, *, count: int = 20, columns: Mapping[str, str] = CME_DISPLAY_COLUMNS, time_label: str = "Time (UTC)"
) -> pd.DataFrame:
    """Selects the most recent CMEs (coronal mass ejections) as a frame for display.

    Args:
        frame: CME catalog events with a UTC DatetimeIndex.
        count: Number of events to keep.
        columns: Stored columns to show mapped to their labels; columns missing in the frame are left out.
        time_label: Label of the event time column.

    Returns:
        The newest events first, with the event time as the first column and a fresh integer index.
    """
    shown = [column for column in columns if column in frame.columns]
    recent = frame.sort_index(ascending=False).head(count)[shown].rename(columns=dict(columns))
    recent.insert(0, time_label, recent.index)
    return recent.reset_index(drop=True)
