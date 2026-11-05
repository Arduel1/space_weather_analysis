from collections.abc import Iterable

import pandas as pd
import plotly.graph_objects as go

from space_weather_analysis.dashboard.scales import LEVEL_COLORS
from space_weather_analysis.scales.noaa import LEVEL_NAMES

# Color of values without a level.
MISSING_LEVEL_COLOR = "#95A5A6"


def level_colors(levels: pd.Series) -> list[str]:
    """Maps nullable level numbers to their colors, missing levels to gray."""
    return levels.map(LEVEL_COLORS).fillna(MISSING_LEVEL_COLOR).tolist()


def add_scale_thresholds(fig: go.Figure, thresholds: Iterable[float], scale_letter: str) -> None:
    """Draws a dotted line with its level label at the lowest value of each scale level.

    Args:
        fig: Figure to draw into.
        thresholds: Ascending lowest values of levels 1 to n.
        scale_letter: Letter of the NOAA scale, G, S or R.
    """
    for level, threshold in enumerate(thresholds, start=1):
        fig.add_hline(
            y=float(threshold),
            line_dash="dot",
            line_width=1,
            line_color=LEVEL_COLORS[level],
            annotation_text=f"{scale_letter}{level} ({LEVEL_NAMES[level]})",
            annotation_position="top right",
            annotation_font_size=10,
        )
