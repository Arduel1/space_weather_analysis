from datetime import datetime, timedelta
from typing import Literal, Self

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from space_weather_analysis.dashboard.freshness import DatasetFreshness, dataset_freshness
from space_weather_analysis.dashboard.labels import Metadata, dataset_title, field_label, field_unit, sources_line
from space_weather_analysis.dashboard.panels.base import CLOSED, PAYLOAD, PanelConfig, PanelContext, PanelPayload, ScaleChoice, TimeWindow
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.dashboard.scales import Level
from space_weather_analysis.dashboard.validity import ValidRange, describe_validity, valid_range
from space_weather_analysis.util.time_utils import SecondsDuration, parse_iso_duration

# Multiple of the typical spacing of the values from which a missing stretch counts as gap and breaks the line
GAP_FACTOR = 3
# Most values of one series in a payload; a longer series keeps the lowest and highest value of each of half as many stretches
MAX_POINTS = 5000


class SeriesConfig(BaseModel):
    """One series of a time series panel.

    Attributes:
        dataset: The dataset ID, which must belong to a feed.
        field: The data field.
        label: The name in legend and hover; empty takes the label of the field from the metadata, else the field name.
        style: How the values are drawn.
        color: The color, any CSS color; None lets Plotly choose.
        valid_min: The smallest valid value, taking precedence over the metadata; None takes the metadata.
        valid_max: The largest valid value, taking precedence over the metadata; None takes the metadata.
    """

    model_config = CLOSED

    dataset: str = Field(min_length=1)
    field: str = Field(min_length=1)
    label: str = ""
    style: Literal["line", "bars", "markers"] = "line"
    color: str | None = None
    valid_min: float | None = None
    valid_max: float | None = None


class TimeseriesPanel(PanelConfig, ScaleChoice, TimeWindow):
    """A chart of one or more fields over time, with the threshold lines of its scale.

    Attributes:
        series: The series, drawn in this order.
        y_axis: The type of the value axis; a log axis leaves out values that are not positive.
        y_range: The fixed range of the value axis, on a log axis in values, not exponents; None fits it to the data.
        y_title: The title of the value axis; empty takes the unit of the first series from the metadata.
        hide_invalid: Whether fill values and values outside the valid range of each series are hidden at first.
    """

    series: list[SeriesConfig] = Field(min_length=1)
    y_axis: Literal["linear", "log"] = "linear"
    y_range: tuple[float, float] | None = None
    y_title: str = ""
    hide_invalid: bool = True

    @model_validator(mode="after")
    def _usable_range(self) -> Self:
        """Rejects a value range that is not ascending, or not positive on a log axis."""
        if self.y_range is not None:
            low, high = self.y_range
            if low >= high or (self.y_axis == "log" and low <= 0):
                raise ValueError("The value range must be ascending, and positive on a log axis.")
        return self


def series_values(frame: pd.DataFrame, series: SeriesConfig, log_axis: bool) -> pd.Series:
    """Selects the numeric values of a series from its frame, without the non-positive ones on a log axis."""
    values = pd.to_numeric(frame[series.field], errors="coerce") if series.field in frame.columns else pd.Series(dtype=float, index=frame.index)
    return values.mask(values <= 0) if log_axis else values


def _nanoseconds(index: pd.Index) -> np.ndarray:
    """Returns the times of a DatetimeIndex as integer nanoseconds."""
    return pd.DatetimeIndex(index).to_numpy(dtype="datetime64[ns]").astype(np.int64)


def break_gaps(values: pd.Series, cadence: timedelta | None) -> pd.Series:
    """Inserts a missing value into every gap, so that a line drawn through the values breaks there instead of bridging it.

    Args:
        values: The values with a sorted UTC DatetimeIndex.
        cadence: The time between two values from the metadata; None takes the median spacing of the values.

    Returns:
        The values with one NaN halfway into every spacing longer than GAP_FACTOR times the cadence.
    """
    if len(values) < 2:
        return values
    nanoseconds = _nanoseconds(values.index)
    spacing = np.diff(nanoseconds)
    typical = pd.Timedelta(cadence).value if cadence is not None else float(np.median(spacing))
    is_gap = spacing > typical * GAP_FACTOR
    if not is_gap.any():
        return values
    # Halfway between the last value before each gap and the first value after it
    gap_middles = pd.DatetimeIndex(nanoseconds[:-1][is_gap] + spacing[is_gap] // 2, tz="UTC")
    return pd.concat([values, pd.Series(float("nan"), index=gap_middles)]).sort_index()


def reduce_points(values: pd.Series, max_points: int = MAX_POINTS) -> pd.Series:
    """Thins a long series for drawing, keeping its peaks and the breaks of its line.

    The time span is cut into max_points / 2 equal stretches, each keeping its lowest and highest value; of each run
    of missing values the first one stays, so a line still breaks there.

    Args:
        values: The values with a sorted UTC DatetimeIndex.
        max_points: The number of values up to which the series stays unchanged.

    Returns:
        The values unchanged up to max_points values, else the kept values in time order.
    """
    if len(values) <= max_points:
        return values
    missing = values.isna()
    run_starts = values[missing & ~missing.shift(fill_value=False)]
    present = values[~missing]
    if present.empty:
        return run_starts
    nanoseconds = _nanoseconds(present.index)
    stretches = max_points // 2
    span = max(int(nanoseconds[-1] - nanoseconds[0]), 1)
    # Float division, as nanoseconds times the number of stretches would overflow 64-bit integers
    stretch = np.minimum(((nanoseconds - nanoseconds[0]) / span * stretches).astype(np.int64), stretches - 1)
    by_stretch = pd.Series(present.to_numpy(dtype=float)).groupby(stretch)
    kept = np.union1d(by_stretch.idxmin().to_numpy(dtype=np.int64), by_stretch.idxmax().to_numpy(dtype=np.int64))
    return pd.concat([present.iloc[kept], run_starts]).sort_index()


class SeriesPayload(BaseModel):
    """The values and labels of one series of a chart payload.

    Attributes:
        label: The name in legend and hover.
        style: How the values are drawn.
        color: The color, any CSS color; None lets the frontend choose.
        unit: The unit of the values; empty if unknown.
        valid: The fill value and bounds that mark values as no measurement.
        validity: The fill value and valid range in words.
        times: The UTC time stamps.
        values: The values, null where the line breaks or a value is missing.
    """

    model_config = PAYLOAD

    label: str
    style: str
    color: str | None
    unit: str
    valid: ValidRange
    validity: str
    times: list[datetime]
    values: list[float | None]


class TimeseriesPayload(PanelPayload):
    """The JSON document of a chart, whose values are not filtered, so the frontend can hide invalid values on request.

    Attributes:
        title: The heading.
        view: The span shown at first, ending at the time of drawing.
        y_axis: The type of the value axis.
        y_range: The fixed range of the value axis in values; None fits it to the data.
        y_title: The title of the value axis.
        levels: The levels of the scale, drawn as threshold lines.
        hide_invalid: Whether invalid values are hidden at first.
        series: The series in drawing order.
        source: The source line of the datasets.
        freshness: How current each dataset with stored values is.
    """

    title: str
    view: SecondsDuration
    y_axis: str
    y_range: tuple[float, float] | None
    y_title: str
    levels: list[Level]
    hide_invalid: bool
    series: list[SeriesPayload]
    source: str
    freshness: list[DatasetFreshness]


def _series_payload(panel: TimeseriesPanel, series: SeriesConfig, frame: pd.DataFrame, record: Metadata) -> SeriesPayload:
    """Builds the payload of one series from the loaded documents of its dataset."""
    values = series_values(frame, series, panel.y_axis == "log")
    if series.style == "line":
        values = break_gaps(values, parse_iso_duration(record.get("time", {}).get("cadence") or ""))
    values = reduce_points(values)
    return SeriesPayload(
        label=field_label(record, series.field, series.label),
        style=series.style,
        color=series.color,
        unit=field_unit(record, series.field),
        valid=valid_range(record, series.field, series.valid_min, series.valid_max),
        validity=describe_validity(record, series.field, series.valid_min, series.valid_max),
        times=pd.DatetimeIndex(values.index).to_pydatetime().tolist(),
        values=values.astype(object).where(values.notna(), None).tolist(),
    )


def build_timeseries(panel: TimeseriesPanel, reader: DashboardReader, context: PanelContext) -> TimeseriesPayload:
    """Builds the JSON document of a chart from the history of its datasets.

    Configured title, labels and axis title take precedence over the metadata, which takes precedence over field names.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export, with the feed interval of each dataset.

    Returns:
        The payload.
    """
    fields_by_dataset: dict[str, list[str]] = {}
    for series in panel.series:
        fields_by_dataset.setdefault(series.dataset, []).append(series.field)
    frames = {dataset: reader.recent(dataset, panel.history, tuple(fields)) for dataset, fields in fields_by_dataset.items()}
    metadata = {dataset: reader.metadata(dataset) for dataset in frames}
    first_record = metadata[panel.series[0].dataset]
    scale = panel.resolved_scale()
    return TimeseriesPayload(
        type=panel.type,
        title=dataset_title(first_record, panel.title),
        view=panel.view,
        y_axis=panel.y_axis,
        y_range=panel.y_range,
        y_title=panel.y_title or field_unit(first_record, panel.series[0].field),
        levels=list(scale.levels) if scale is not None else [],
        hide_invalid=panel.hide_invalid,
        series=[_series_payload(panel, series, frames[series.dataset], metadata[series.dataset]) for series in panel.series],
        source=sources_line(metadata[series.dataset] for series in panel.series),
        freshness=[
            dataset_freshness(dataset, frame.index.max().to_pydatetime(), metadata[dataset], context.feed_intervals.get(dataset))
            for dataset, frame in frames.items()
            if not frame.empty
        ],
    )
