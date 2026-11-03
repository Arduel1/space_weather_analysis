import pandas as pd
import plotly.graph_objects as go

from space_weather_analysis.scales.noaa import (
    FLARE_CLASS_BASES_W_M2,
    FLARE_CLASS_LETTERS,
    R_THRESHOLDS_W_M2,
    S_THRESHOLDS_PFU,
    flare_class,
)
from streamlit_app.figures.scale_style import add_scale_thresholds

# Fill colors of the flare class bands B to X, stronger for higher classes.
_FLARE_BAND_COLORS = {
    "B": "rgba(52, 152, 219, 0.05)",
    "C": "rgba(46, 204, 113, 0.07)",
    "M": "rgba(230, 126, 34, 0.10)",
    "X": "rgba(231, 76, 60, 0.12)",
}
# Upper edge of the X band in W/m^2, the top of the X-ray axis.
_XRAY_AXIS_TOP_W_M2 = 1e-2
_LEGEND_ON_TOP = {"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1}


def _positive(values: pd.Series) -> pd.Series:
    """Converts values to numbers and hides non-positive ones, which a log axis cannot show."""
    numbers = pd.to_numeric(values, errors="coerce")
    return numbers.mask(numbers <= 0)


def xray_figure(frame: pd.DataFrame, *, long_column: str = "Long_Wave", short_column: str = "Short_Wave") -> go.Figure:
    """Builds a log plot of the GOES X-ray flux with flare class bands and NOAA radio blackout levels.

    GOES are the NOAA Geostationary Operational Environmental Satellites. The long channel (0.1-0.8 nm)
    defines the flare class and the R level, the short channel covers 0.05-0.4 nm.

    Args:
        frame: Flux values in W/m^2 with a UTC DatetimeIndex.
        long_column: Column of the 0.1-0.8 nm channel.
        short_column: Column of the 0.05-0.4 nm channel.

    Returns:
        One line trace per channel, a band per flare class B to X and a line at the lowest flux of each
        R level.
    """
    long_flux = _positive(frame[long_column])
    short_flux = _positive(frame[short_column])
    fig = go.Figure()
    fig.add_trace(
        go.Scattergl(
            x=long_flux.index,
            y=long_flux,
            customdata=flare_class(long_flux).fillna(""),
            mode="lines",
            name="Long 0.1-0.8 nm",
            line={"color": "#27AE60", "width": 1.5},
            hovertemplate="%{y:.2e} W/m² %{customdata}",
        )
    )
    fig.add_trace(
        go.Scattergl(
            x=short_flux.index,
            y=short_flux,
            mode="lines",
            name="Short 0.05-0.4 nm",
            line={"color": "#0068C9", "width": 1},
            hovertemplate="%{y:.2e} W/m²",
        )
    )
    band_tops = [*FLARE_CLASS_BASES_W_M2[2:], _XRAY_AXIS_TOP_W_M2]
    for letter, bottom, top in zip(FLARE_CLASS_LETTERS[1:], FLARE_CLASS_BASES_W_M2[1:], band_tops, strict=True):
        fig.add_hrect(
            y0=float(bottom),
            y1=float(top),
            fillcolor=_FLARE_BAND_COLORS[str(letter)],
            layer="below",
            line_width=0,
            annotation_text=f"{letter} class",
            annotation_position="top left",
            annotation_font_size=10,
        )
    add_scale_thresholds(fig, R_THRESHOLDS_W_M2, "R")
    fig.update_layout(
        title="GOES X-ray flux",
        xaxis_title="UTC time",
        yaxis={"title": "Flux (W/m²)", "type": "log", "range": [-9, -2]},
        hovermode="x unified",
        legend=_LEGEND_ON_TOP,
    )
    return fig


def proton_figure(frame: pd.DataFrame, *, proton_column: str = "P10") -> go.Figure:
    """Builds a log plot of the GOES >=10 MeV integral proton flux with NOAA solar radiation storm levels.

    Args:
        frame: Flux values in pfu (particle flux unit, protons / (cm² s sr)) with a UTC DatetimeIndex.
        proton_column: Column of the >=10 MeV channel.

    Returns:
        One line trace and a line at the lowest flux of each S level.
    """
    proton_flux = _positive(frame[proton_column])
    fig = go.Figure(
        go.Scattergl(
            x=proton_flux.index,
            y=proton_flux,
            mode="lines",
            name="Protons >=10 MeV",
            line={"color": "#FF4B4B", "width": 1.5},
            hovertemplate="%{y:.2e} pfu",
        )
    )
    add_scale_thresholds(fig, S_THRESHOLDS_PFU, "S")
    fig.update_layout(
        title="GOES proton flux >=10 MeV",
        xaxis_title="UTC time",
        yaxis={"title": "Flux (pfu)", "type": "log", "range": [-1, 5.5]},
        hovermode="x unified",
        legend=_LEGEND_ON_TOP,
    )
    return fig
