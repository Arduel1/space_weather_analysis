from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Literal, Self

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from space_weather_analysis.dashboard.derived import DERIVED
from space_weather_analysis.dashboard.freshness import DatasetFreshness
from space_weather_analysis.dashboard.labels import Metadata, dataset_title, field_label, field_unit
from space_weather_analysis.dashboard.panels.base import PAYLOAD, PanelConfig, PanelContext, PanelPayload, ScaleChoice, TimeWindow
from space_weather_analysis.dashboard.panels.cycles import find_minima
from space_weather_analysis.dashboard.panels.series import (
    SeriesConfig,
    SourceRef,
    break_gaps,
    frames_for,
    json_points,
    masked_input,
    read_freshness,
    reduce_points,
    series_payload_values,
)
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.dashboard.scales import CLASS_TICKS, Level
from space_weather_analysis.dashboard.validity import ValidRange, describe_validity, valid_range
from space_weather_analysis.util.time_utils import SecondsDuration, parse_iso_duration


class RowConfig(ScaleChoice):
    """One chart area: its series share one value axis.

    Attributes:
        title: The heading of the area in a stacked chart; empty for none.
        series: The series, drawn in this order.
        y_axis: The type of the value axis; a log axis leaves out values that are not positive.
        y_range: The fixed range of the value axis, on a log axis in values, not exponents; None fits it to the data.
        y_title: The title of the value axis; empty takes the unit of the first series.
        class_ticks: The name of a class tick set written at the right edge, such as the flare classes; None for none.
    """

    title: str = ""
    series: list[SeriesConfig] = Field(min_length=1)
    y_axis: Literal["linear", "log"] = "linear"
    y_range: tuple[float, float] | None = None
    y_title: str = ""
    class_ticks: str | None = None

    @model_validator(mode="after")
    def _usable_axis(self) -> Self:
        """Rejects a value range that is not ascending or not positive on a log axis, and an unknown class tick set."""
        if self.y_range is not None:
            low, high = self.y_range
            if low >= high or (self.y_axis == "log" and low <= 0):
                raise ValueError("The value range must be ascending, and positive on a log axis.")
        if self.class_ticks is not None and self.class_ticks not in CLASS_TICKS:
            raise ValueError(f"Unknown class ticks '{self.class_ticks}'; known are {sorted(CLASS_TICKS)}.")
        return self


class StackPanel(PanelConfig, TimeWindow):
    """Chart areas stacked over one shared time axis, so that cause and effect line up in time.

    Attributes:
        rows: The chart areas from the top.
        hide_invalid: Whether fill values and values outside the valid range of each series are hidden at first.
        cycle_marks: The smoothed cycle series whose minima and maxima are marked as vertical lines; None for no marks.
    """

    rows: list[RowConfig] = Field(min_length=1)
    hide_invalid: bool = True
    cycle_marks: SourceRef | None = None


class SeriesPayload(BaseModel):
    """The values and labels of one series of a chart payload.

    Attributes:
        label: The name in legend and hover.
        style: How the values are drawn.
        color: The color, any CSS color; None lets the frontend choose.
        unit: The unit of the values; empty if unknown.
        valid: The fill value and bounds that mark values as no measurement.
        validity: The fill value and valid range in words.
        reference: The publication of the formula of a derived quantity; None for a stored field.
        formula: The formula of a derived quantity; None for a stored field.
        shade_negative: Whether the area between zero and the negative values is shaded.
        envelope: Whether the band between minus and plus the values is drawn.
        color_by_scale: Whether each bar or marker takes the color of its level.
        hatched: Whether bars are hatched.
        dash: The line pattern, solid, dash or dot.
        times: The UTC time stamps.
        values: The values, null where the line breaks or a value is missing.
        labels: The text written next to each marker; None without label field.
    """

    model_config = PAYLOAD

    label: str
    style: str
    color: str | None
    unit: str
    valid: ValidRange
    validity: str
    reference: str | None = None
    formula: str | None = None
    shade_negative: bool = False
    envelope: bool = False
    color_by_scale: bool = False
    hatched: bool = False
    dash: str = "solid"
    times: list[datetime]
    values: list[float | None]
    labels: list[str | None] | None = None


class TickPayload(BaseModel):
    """One class tick written at the right edge of a chart area.

    Attributes:
        value: The value the tick marks.
        label: The class name.
    """

    model_config = PAYLOAD

    value: float
    label: str


class RowPayload(BaseModel):
    """One chart area of a chart payload.

    Attributes:
        title: The heading of the area; empty for none.
        y_axis: The type of the value axis.
        y_range: The fixed range of the value axis in values; None fits it to the data.
        y_title: The title of the value axis.
        levels: The levels of the scale, drawn as threshold lines.
        quiet: The level of values below the first level; None without one.
        ticks: The class ticks written at the right edge.
        series: The series in drawing order.
    """

    model_config = PAYLOAD

    title: str
    y_axis: str
    y_range: tuple[float, float] | None
    y_title: str
    levels: list[Level]
    quiet: Level | None
    ticks: list[TickPayload]
    series: list[SeriesPayload]


class MarkPayload(BaseModel):
    """A vertical line marking a moment on the time axis of a chart.

    Attributes:
        time: The moment.
        label: The name written at the line.
    """

    model_config = PAYLOAD

    time: datetime
    label: str


class TimeseriesPayload(PanelPayload):
    """The JSON document of a chart of one or more stacked areas, whose values are not filtered, so the frontend can hide invalid values.

    Attributes:
        title: The heading.
        view: The span shown at first, ending at the time of drawing.
        ahead: The span shown after the time of drawing, for model values of the coming days.
        hide_invalid: Whether invalid values are hidden at first.
        rows: The chart areas from the top, sharing the time axis.
        marks: The moments marked as vertical lines, such as the minima and maxima of the solar cycle.
        freshness: How current each dataset with stored values is.
    """

    title: str
    view: SecondsDuration
    ahead: SecondsDuration = timedelta(0)
    hide_invalid: bool
    rows: list[RowPayload]
    marks: list[MarkPayload] = Field(default_factory=list)
    freshness: list[DatasetFreshness]


def _marker_labels(series: SeriesConfig, frames: Mapping[str, pd.DataFrame], values: pd.Series) -> list[str | None] | None:
    """Reads the label field text at the time of each value; None without label field or for a derived quantity."""
    if not series.label_field or series.derived is not None or series.label_field not in frames[series.dataset].columns:
        return None
    labels_by_time = frames[series.dataset][series.label_field]
    return [None if pd.isna(text) else str(text) for text in labels_by_time.reindex(values.index)]


def _series_payload(row: RowConfig, series: SeriesConfig, frames: Mapping[str, pd.DataFrame], metadata: Mapping[str, Metadata]) -> SeriesPayload:
    """Builds the payload of one series from the loaded documents of its datasets."""
    values = series_payload_values(series, frames, metadata, row.y_axis == "log")
    labels = _marker_labels(series, frames, values)
    # Labelled markers keep every value, so that each label stays beside its marker
    if labels is None:
        if series.style == "line":
            # A resampled line breaks where steps are missing, a stored one where the values pause beyond their cadence
            record = metadata.get(series.dataset, {})
            values = break_gaps(values, series.resample or parse_iso_duration(record.get("time", {}).get("cadence") or ""))
        values = reduce_points(values)
    times, numbers = json_points(values)
    styling = {
        "style": series.style,
        "color": series.color,
        "shade_negative": series.shade_negative,
        "envelope": series.envelope,
        "color_by_scale": series.color_by_scale,
        "hatched": series.hatched,
        "dash": series.dash,
    }
    if series.derived is not None:
        quantity = DERIVED[series.derived]
        return SeriesPayload(
            label=series.label or quantity.label,
            unit=quantity.units,
            valid=ValidRange(),
            validity="computed from the measured values",
            reference=quantity.reference,
            formula=quantity.formula,
            times=times,
            values=numbers,
            **styling,
        )
    record = metadata[series.dataset]
    return SeriesPayload(
        label=series.label or field_label(record, series.field),
        # A count of values or of exceedances has no unit, and a scaled field differs from the unit of the stored one
        unit=field_unit(record, series.field) if series.factor == 1.0 and series.stat != "count" and series.above is None else "",
        valid=valid_range(record, series.field, series.valid_min, series.valid_max) if series.resample is None else ValidRange(),
        validity=describe_validity(record, series.field, series.valid_min, series.valid_max),
        times=times,
        values=numbers,
        labels=labels,
        **styling,
    )


def _after_previous(payload: SeriesPayload, previous: SeriesPayload) -> SeriesPayload:
    """Keeps the values of a series after the newest value of the series before it; all of them if that one has none."""
    known = [time for time, value in zip(previous.times, previous.values, strict=True) if value is not None]
    if not known:
        return payload
    # Times, values and labels are parallel lists, so each is cut at the same newest time of the previous series
    kept = [(time, value) for time, value in zip(payload.times, payload.values, strict=True) if time > known[-1]]
    labels = None if payload.labels is None else [label for time, label in zip(payload.times, payload.labels, strict=True) if time > known[-1]]
    return payload.model_copy(update={"times": [time for time, _ in kept], "values": [value for _, value in kept], "labels": labels})


def row_payload(row: RowConfig, frames: Mapping[str, pd.DataFrame], metadata: Mapping[str, Metadata]) -> RowPayload:
    """Builds the payload of one chart area."""
    scale = row.resolved_scale()
    series: list[SeriesPayload] = []
    for config in row.series:
        payload = _series_payload(row, config, frames, metadata)
        # A continuing series picks up where the series before it ends, such as a nowcast after an official index
        series.append(_after_previous(payload, series[-1]) if config.continues and series else payload)
    return RowPayload(
        title=row.title,
        y_axis=row.y_axis,
        y_range=row.y_range,
        y_title=row.y_title or (series[0].unit if series else ""),
        levels=list(scale.levels) if scale is not None else [],
        quiet=scale.quiet if scale is not None else None,
        ticks=[TickPayload(value=value, label=label) for value, label in CLASS_TICKS.get(row.class_ticks or "", ())],
        series=series,
    )


# Shortest time between two minima or two maxima of a solar cycle, well below the cycle length of about 11 years, of
# which the shortest observed cycles last about 9 (Hathaway, D. H. (2015), The Solar Cycle, Living Rev. Sol. Phys. 12,
# 4, doi:10.1007/lrsp-2015-4)
CYCLE_SEPARATION = timedelta(days=8 * 365)


def cycle_marks(source: SourceRef | None, history: timedelta, reader: DashboardReader) -> list[MarkPayload]:
    """Finds the minima and maxima of a smoothed cycle series in a history, oldest first.

    Args:
        source: The smoothed series; None for no marks.
        history: The span read.
        reader: The source of documents and metadata.

    Returns:
        One mark per minimum and maximum.
    """
    if source is None:
        return []
    values = masked_input(reader.recent(source.dataset, history, (source.field,)), source, reader.metadata(source.dataset))
    # The maxima of the values are the minima of the negated values
    marks = [MarkPayload(time=time.to_pydatetime(), label="cycle minimum") for time in find_minima(values, CYCLE_SEPARATION)]
    marks += [MarkPayload(time=time.to_pydatetime(), label="cycle maximum") for time in find_minima(-values, CYCLE_SEPARATION)]
    return sorted(marks, key=lambda mark: mark.time)


def load_frames(rows: list[RowConfig], history: timedelta, reader: DashboardReader) -> dict[str, pd.DataFrame]:
    """Reads the history of every dataset the series of the rows read, once per dataset with all fields they need."""
    refs = [ref for row in rows for series in row.series for ref in series.stored_fields()]
    return frames_for(refs, history, reader)


def build_stack(panel: StackPanel, reader: DashboardReader, context: PanelContext) -> TimeseriesPayload:
    """Builds the JSON document of stacked chart areas sharing one time axis from the history of their datasets.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export, with the feed interval of each dataset.

    Returns:
        The payload.
    """
    frames = load_frames(panel.rows, panel.history, reader)
    metadata = {dataset: reader.metadata(dataset) for dataset in frames}
    # Without a configured title the chart takes the title of the dataset read first
    first_dataset = next(iter(frames), "")
    return TimeseriesPayload(
        type=panel.type,
        title=dataset_title(metadata.get(first_dataset, {}), panel.title),
        view=panel.view,
        ahead=panel.ahead,
        hide_invalid=panel.hide_invalid,
        rows=[row_payload(row, frames, metadata) for row in panel.rows],
        marks=cycle_marks(panel.cycle_marks, panel.history, reader),
        freshness=read_freshness(frames.items(), metadata.__getitem__, context),
    )
