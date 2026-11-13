from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from typing import Literal, Self

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from space_weather_analysis.dashboard.derived import DERIVED, compute
from space_weather_analysis.dashboard.freshness import DatasetFreshness, dataset_freshness
from space_weather_analysis.dashboard.labels import Metadata
from space_weather_analysis.dashboard.panels.base import CLOSED, PanelContext
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.dashboard.validity import valid_mask, valid_range
from space_weather_analysis.util.time_utils import IsoDuration

# Multiple of the typical spacing of the values from which a missing stretch counts as gap and breaks the line
GAP_FACTOR = 3
# Most values of one series in a payload; a longer series keeps the lowest and highest value of each of half as many stretches
MAX_POINTS = 5000

# How the values of one resampling step are combined
type Statistic = Literal["mean", "median", "max", "min", "count", "sum"]


class SourceRef(BaseModel):
    """One stored field of a dataset.

    Attributes:
        dataset: The dataset ID, which must belong to a feed.
        field: The data field.
    """

    model_config = CLOSED

    dataset: str = Field(min_length=1)
    field: str = Field(min_length=1)


class SeriesConfig(BaseModel):
    """One series of a chart: a stored field, or a derived quantity computed from stored fields.

    Attributes:
        dataset: The dataset ID of a stored field, which must belong to a feed; empty for a derived quantity.
        field: The data field of a stored field; empty for a derived quantity.
        derived: The name of a derived quantity of the derived registry; None for a stored field.
        inputs: The stored field of each input of the derived quantity, by input name.
        label: The name in legend and hover; empty takes the label from the metadata or the derived quantity.
        style: How the values are drawn; area fills down to zero.
        color: The color, any CSS color; None takes the next color of the frontend palette.
        valid_min: The smallest valid value, taking precedence over the metadata; None takes the metadata.
        valid_max: The largest valid value, taking precedence over the metadata; None takes the metadata.
        factor: Multiplies the stored values, for fields stored in scaled units such as Kp times ten.
        shade_negative: Whether the area between zero and the negative values is shaded.
        envelope: Whether the band between minus and plus the values is drawn instead of the line.
        color_by_scale: Whether each bar or marker takes the color of the level of the scale of its row.
        label_field: A text field of the same dataset written next to each marker; empty for none.
        resample: The step the values are combined in, such as one year; None draws every value.
        stat: How the values of one step are combined.
        above: With resampling, counts the values above this bound instead of using the values.
        continues: Whether the series only shows its values after the newest value of the series before it in the row,
            such as a nowcast filling the time an official index is not published yet.
        hatched: Whether bars are hatched, marking values of lesser standing such as estimates.
        shift_field: A field of the same dataset holding a time in days by which each value is moved, such as the time
            the Sun takes to turn a solar wind stream from one spacecraft to another; empty for none.
        dash: The line pattern: dashed or dotted lines tell apart several series of one quantity in its theme color,
            such as model values beside measured ones.
    """

    model_config = CLOSED

    dataset: str = ""
    field: str = ""
    derived: str | None = None
    inputs: dict[str, SourceRef] = Field(default_factory=dict)
    label: str = ""
    style: Literal["line", "bars", "markers", "area"] = "line"
    color: str | None = None
    valid_min: float | None = None
    valid_max: float | None = None
    factor: float = 1.0
    shade_negative: bool = False
    envelope: bool = False
    color_by_scale: bool = False
    label_field: str = ""
    resample: IsoDuration | None = None
    stat: Statistic = "mean"
    above: float | None = None
    continues: bool = False
    hatched: bool = False
    shift_field: str = ""
    dash: Literal["solid", "dash", "dot"] = "solid"

    @model_validator(mode="after")
    def _stored_or_derived(self) -> Self:
        """Rejects a series that names neither a stored field nor a known derived quantity with all its inputs."""
        if self.derived is None:
            if not self.dataset or not self.field:
                raise ValueError("A series names a dataset and a field, or a derived quantity with its inputs.")
            return self
        quantity = DERIVED.get(self.derived)
        if quantity is None:
            raise ValueError(f"Unknown derived quantity '{self.derived}'; known are {sorted(DERIVED)}.")
        if set(self.inputs) != set(quantity.inputs):
            raise ValueError(f"Derived quantity '{self.derived}' takes the inputs {list(quantity.inputs)}.")
        return self

    def stored_fields(self) -> list[SourceRef]:
        """Lists the stored fields the series reads: the inputs of a derived quantity, else its field, label field and shift field."""
        if self.derived is not None:
            return list(self.inputs.values())
        fields = (self.field, self.label_field, self.shift_field)
        return [SourceRef(dataset=self.dataset, field=field) for field in fields if field]


def series_values(frame: pd.DataFrame, series: SeriesConfig, log_axis: bool) -> pd.Series:
    """Selects the numeric values of a stored series from its frame, scaled by its factor, without the non-positive ones on a log axis."""
    values = pd.to_numeric(frame[series.field], errors="coerce") if series.field in frame.columns else pd.Series(dtype=float, index=frame.index)
    values = values * series.factor
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

    Args:
        values: The values with a sorted UTC DatetimeIndex.
        max_points: The number of values up to which the series stays unchanged.

    Returns:
        The values unchanged up to max_points values, else the kept values in time order.
    """
    if len(values) <= max_points:
        return values
    # Of each run of missing values the first one stays, so a line still breaks there
    missing = values.isna()
    run_starts = values[missing & ~missing.shift(fill_value=False)]
    present = values[~missing]
    if present.empty:
        return run_starts
    # The time span is cut into equal stretches, each keeping its lowest and highest value, two points per stretch
    nanoseconds = _nanoseconds(present.index)
    stretches = max_points // 2
    span = max(int(nanoseconds[-1] - nanoseconds[0]), 1)
    # Float division, as nanoseconds times the number of stretches would overflow 64-bit integers
    stretch = np.minimum(((nanoseconds - nanoseconds[0]) / span * stretches).astype(np.int64), stretches - 1)
    # A plain position index, so that idxmin and idxmax name positions for iloc; union1d keeps a point that is both once
    by_stretch = pd.Series(present.to_numpy(dtype=float)).groupby(stretch)
    kept = np.union1d(by_stretch.idxmin().to_numpy(dtype=np.int64), by_stretch.idxmax().to_numpy(dtype=np.int64))
    return pd.concat([present.iloc[kept], run_starts]).sort_index()


def index_times(values: pd.Series | pd.DataFrame) -> list[datetime]:
    """Returns the times of the UTC DatetimeIndex of a series or frame as datetimes for a payload."""
    return pd.DatetimeIndex(values.index).to_pydatetime().tolist()


def json_points(values: pd.Series) -> tuple[list[datetime], list[float | None]]:
    """Splits a series into its time stamps and its values for a payload, with None in place of a missing value."""
    return index_times(values), values.astype(object).where(values.notna(), None).tolist()


def resample_rule(step: timedelta) -> str | pd.Timedelta:
    """Maps a resampling step to calendar years or months where it has their length, else to a fixed step."""
    # YS and MS are the pandas rules for steps starting at the first day of each year and month
    if step >= timedelta(days=365):
        return "YS"
    if step >= timedelta(days=28):
        return "MS"
    return pd.Timedelta(step)


def resample(values: pd.Series, step: timedelta, stat: Statistic, above: float | None) -> pd.Series:
    """Combines the values of each step, or counts the values above a bound, with the step start as time stamp.

    Args:
        values: The values with a UTC DatetimeIndex.
        step: The length of a step; a year or a month is a calendar year or month.
        stat: How the values of a step are combined.
        above: The bound whose exceedances are counted instead; None combines the values.

    Returns:
        One value per step that holds values.
    """
    present = values.dropna()
    # A field without stored values comes without time index, which has nothing to combine
    if present.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    # Counting exceedances is the sum of 1 for each value above the bound and 0 for the others
    if above is not None:
        present = (present > above).astype(float)
        stat = "sum"
    steps = present.resample(resample_rule(step))
    combined = pd.Series(steps.agg(stat))
    # Steps without values would hold 0 for sum and count, which is no measured value
    return combined[steps.count() > 0]


def shift_values(values: pd.Series, frame: pd.DataFrame, shift_field: str) -> pd.Series:
    """Moves each value by the days its document holds in a shift field; values without shift are left out.

    Args:
        values: The values with a UTC DatetimeIndex, from the documents of the frame.
        frame: The documents of the dataset.
        shift_field: The field holding the shift in days.

    Returns:
        The moved values, sorted by their new times.
    """
    if shift_field not in frame.columns:
        return values.iloc[:0]
    # The shift of each value comes from the document at the same time
    days = pd.to_numeric(frame[shift_field], errors="coerce").reindex(values.index)
    kept = days.notna() & values.notna()
    moved = pd.DatetimeIndex(values.index[kept]) + pd.to_timedelta(days[kept].to_numpy(dtype=float), unit="D")
    return pd.Series(values[kept].to_numpy(), index=moved).sort_index()


def masked_input(frame: pd.DataFrame, ref: SourceRef, record: Metadata) -> pd.Series:
    """Selects the numeric values of a stored field without its fill value and the values outside its valid range."""
    if ref.field not in frame.columns:
        return pd.Series(dtype=float)
    values = pd.to_numeric(frame[ref.field], errors="coerce")
    return values.where(valid_mask(values, valid_range(record, ref.field)))


def series_payload_values(
    series: SeriesConfig, frames: Mapping[str, pd.DataFrame], metadata: Mapping[str, Metadata], log_axis: bool = False
) -> pd.Series:
    """Computes the values of a series: its stored field or its derived quantity, resampled where configured."""
    # Inputs of a derived quantity lose their invalid values first, so that a fill value enters no formula
    if series.derived is not None:
        columns = {name: masked_input(frames[ref.dataset], ref, metadata[ref.dataset]) for name, ref in series.inputs.items()}
        values = compute(series.derived, columns) * series.factor
        values = values.mask(values <= 0) if log_axis else values
    else:
        values = series_values(frames[series.dataset], series, log_axis)
        if series.shift_field:
            values = shift_values(values, frames[series.dataset], series.shift_field)
    if series.resample is not None:
        values = resample(values, series.resample, series.stat, series.above)
    return values


def frames_for(refs: Iterable[SourceRef], history: timedelta, reader: DashboardReader) -> dict[str, pd.DataFrame]:
    """Reads the history of every dataset of the given fields, once per dataset with all its fields in the order given."""
    fields_by_dataset: dict[str, list[str]] = {}
    for ref in refs:
        fields = fields_by_dataset.setdefault(ref.dataset, [])
        if ref.field not in fields:
            fields.append(ref.field)
    return {dataset: reader.recent(dataset, history, tuple(fields)) for dataset, fields in fields_by_dataset.items()}


def _newest_until(values: pd.Series | pd.DataFrame, now: datetime) -> datetime:
    """Returns the newest time of the index of read values, at most the time of drawing."""
    return min(pd.DatetimeIndex(values.index).max().to_pydatetime(), now)


def read_freshness(
    read: Iterable[tuple[str, pd.Series | pd.DataFrame]], metadata_of: Callable[[str], Metadata], context: PanelContext
) -> list[DatasetFreshness]:
    """Describes how current each read dataset with values is, in the order read.

    Args:
        read: The dataset ID and the values read of it, with a UTC DatetimeIndex; datasets without values are left out.
        metadata_of: Returns the metadata record of a dataset.
        context: The context of the export, with the time of the drawing and the feed interval of each dataset.

    Returns:
        One description per dataset with values; values ahead of now count as newest value at the time of drawing.
    """
    return [
        dataset_freshness(dataset, _newest_until(values, context.now), metadata_of(dataset), context.feed_intervals.get(dataset))
        for dataset, values in read
        if not values.empty
    ]
