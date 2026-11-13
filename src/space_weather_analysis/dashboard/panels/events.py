from collections.abc import Mapping
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from space_weather_analysis.dashboard.derived import DERIVED
from space_weather_analysis.dashboard.labels import Metadata, field_label, field_label_with_unit, field_unit
from space_weather_analysis.dashboard.panels.base import PAYLOAD, PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.panels.series import SeriesConfig, SourceRef, frames_for, masked_input, series_payload_values
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.util.time_utils import IsoDuration, SecondsDuration

# Percentiles of the lower quartile, the median and the upper quartile, the middle half of the events around the median
QUARTILES = (25, 50, 75)


class EventRule(SourceRef):
    """What makes an event: a stored field beyond a threshold, such as a geomagnetic storm as Dst below -100 nT.

    Attributes:
        threshold: The value the field must pass.
        below: Whether an event lies below the threshold; False for above.
        separation: The shortest quiet time between two events; closer stretches beyond the threshold are one event.
        factor: Multiplies the stored values before the comparison, for fields stored in scaled units.
        label: The name of the field in the payload; empty takes the label from the metadata.
    """

    threshold: float
    below: bool = True
    separation: IsoDuration = timedelta(days=3)
    factor: float = 1.0
    label: str = ""


def find_events(values: pd.Series, threshold: float, below: bool, separation: timedelta) -> pd.Series:
    """Finds the events of a series and the most extreme value of each.

    Args:
        values: The values with a UTC DatetimeIndex.
        threshold: The value an event must pass.
        below: Whether an event lies below the threshold; False for above.
        separation: The longest gap within one event.

    Returns:
        The most extreme value of each event at its time, oldest first; empty without events.
    """
    present = values.dropna().sort_index()
    beyond = present[present < threshold] if below else present[present > threshold]
    if beyond.empty:
        return beyond
    # The values beyond the threshold form one event as long as no gap longer than the separation lies between them; the
    # running count of such gaps numbers the events
    event_numbers = (beyond.index.to_series().diff() > pd.Timedelta(separation)).cumsum().to_numpy()
    grouped = beyond.groupby(event_numbers)
    extreme_times = grouped.idxmin() if below else grouped.idxmax()
    return beyond.loc[pd.Index(extreme_times)]


def _rule_events(rule: EventRule, frames: Mapping[str, pd.DataFrame], metadata: Mapping[str, Metadata]) -> pd.Series:
    """Finds the events of a rule in the loaded documents."""
    values = masked_input(frames[rule.dataset], rule, metadata[rule.dataset]) * rule.factor
    return find_events(values, rule.threshold, rule.below, rule.separation)


def _series_name(series: SeriesConfig, metadata: Mapping[str, Metadata]) -> tuple[str, str]:
    """Names a series and its unit: the configured label, else the derived quantity or the metadata of its field."""
    if series.derived is not None:
        quantity = DERIVED[series.derived]
        return series.label or quantity.label, quantity.units
    record = metadata[series.dataset]
    # Scaled values no longer have the unit of the stored field
    unit = field_unit(record, series.field) if series.factor == 1.0 else ""
    return series.label or field_label(record, series.field), unit


def _finite_or_none(values: np.ndarray) -> list[float | None]:
    """Turns an array into a list with None in place of every missing value."""
    return [float(value) if np.isfinite(value) else None for value in values]


class EpochPanel(PanelConfig):
    """The typical course of several quantities around the events of a long history, all events laid over each other.

    Each row shows the median and the middle half (25 to 75 %) of all events at each time relative to the event, and
    the newest event on its own. This superposed epoch analysis shows what typically comes before and after an event.

    Attributes:
        events: What makes an event; its most extreme value is time zero.
        history: The span of history the events are taken from, ending with the newest stored document.
        before: The span shown before each event.
        after: The span shown after each event.
        step: The time grid all values are averaged on.
        rows: The quantities shown, one chart area each.
        caption: What the chart shows and why, with the publications it relies on; empty for the plain count.
    """

    events: EventRule
    history: IsoDuration
    before: IsoDuration = timedelta(days=2)
    after: IsoDuration = timedelta(days=4)
    step: IsoDuration = timedelta(hours=1)
    rows: list[SeriesConfig] = Field(min_length=1)
    caption: str = ""


class EpochRowPayload(BaseModel):
    """One quantity of a superposed epoch analysis.

    Attributes:
        label: The name of the quantity.
        unit: Its unit; empty if unknown.
        reference: The publication of the formula of a derived quantity; None for a stored field.
        formula: The formula of a derived quantity; None for a stored field.
        median: The median over all events per time offset, null where no event has a value.
        lower: The 25th percentile per time offset.
        upper: The 75th percentile per time offset.
        newest: The values of the newest event per time offset, null where none are stored yet.
        color: The color of the row, a CSS color or a theme color token; None for the first palette color.
    """

    model_config = PAYLOAD

    label: str
    unit: str
    reference: str | None
    formula: str | None
    median: list[float | None]
    lower: list[float | None]
    upper: list[float | None]
    newest: list[float | None]
    color: str | None = None


class EpochPayload(PanelPayload):
    """The JSON document of a superposed epoch analysis.

    Attributes:
        title: The heading.
        event_label: The name of the field defining the events.
        threshold: The value an event passes.
        below: Whether events lie below the threshold.
        step: The time between two offsets.
        offsets: The time offsets from the event, as multiples of the step.
        events: The number of events laid over each other.
        newest: The time of the newest event; None without events.
        since: The time of the oldest event; None without events.
        rows: The quantities.
        caption: What the chart shows and why; empty for the plain count.
    """

    title: str
    event_label: str
    threshold: float
    below: bool
    step: SecondsDuration
    offsets: list[int]
    events: int
    newest: datetime | None
    since: datetime | None
    rows: list[EpochRowPayload]
    caption: str = ""


def _aligned(values: pd.Series, epochs: pd.DatetimeIndex, offsets: np.ndarray, step: timedelta) -> np.ndarray:
    """Cuts the stretch around each epoch out of a series averaged on the time grid, one row per epoch."""
    grid = values.dropna().resample(pd.Timedelta(step)).mean()
    # Each epoch is floored to the grid, so that its offsets land on grid times; plain UTC datetime64 values, as numpy
    # cannot add time offsets to time stamps with a time zone
    starts = epochs.floor(f"{int(step.total_seconds())}s").tz_convert(None).to_numpy()
    # One row of times per epoch and one column per offset, read from the grid in one reindex and shaped back
    times = starts[:, None] + offsets[None, :] * pd.Timedelta(step).to_timedelta64()
    return grid.reindex(pd.DatetimeIndex(times.ravel()).tz_localize("UTC")).to_numpy(dtype=float).reshape(times.shape)


def build_epoch(panel: EpochPanel, reader: DashboardReader, context: PanelContext) -> EpochPayload:
    """Builds the JSON document of a superposed epoch analysis from the history of its datasets.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export.

    Returns:
        The payload.
    """
    del context
    refs = [panel.events, *(ref for series in panel.rows for ref in series.stored_fields())]
    frames = frames_for(refs, panel.history, reader)
    metadata = {dataset: reader.metadata(dataset) for dataset in frames}
    events = _rule_events(panel.events, frames, metadata)
    # Offsets in steps from the event, from before it to after it including the event itself at 0
    offsets = np.arange(-int(panel.before / panel.step), int(panel.after / panel.step) + 1)
    epochs = pd.DatetimeIndex(events.index)
    rows = []
    for series in panel.rows:
        label, unit = _series_name(series, metadata)
        aligned = _aligned(series_payload_values(series, frames, metadata), epochs, offsets, panel.step)
        empty = np.full(len(offsets), np.nan)
        has_values = len(epochs) > 0 and np.isfinite(aligned).any()
        # Offsets without any value would warn in nanpercentile; they stay missing
        lower, median, upper = np.nanpercentile(aligned, QUARTILES, axis=0) if has_values else (empty, empty, empty)
        rows.append(
            EpochRowPayload(
                label=label,
                unit=unit,
                reference=DERIVED[series.derived].reference if series.derived is not None else None,
                formula=DERIVED[series.derived].formula if series.derived is not None else None,
                median=_finite_or_none(median),
                lower=_finite_or_none(lower),
                upper=_finite_or_none(upper),
                # The events are sorted by time, so the last row is the newest event
                newest=_finite_or_none(aligned[-1]) if len(epochs) > 0 else [None] * len(offsets),
                color=series.color,
            )
        )
    return EpochPayload(
        type=panel.type,
        title=panel.title,
        event_label=field_label_with_unit(metadata[panel.events.dataset], panel.events.field, panel.events.label, scaled=panel.events.factor != 1.0),
        threshold=panel.events.threshold,
        below=panel.events.below,
        step=panel.step,
        offsets=offsets.tolist(),
        events=len(epochs),
        newest=epochs[-1].to_pydatetime() if len(epochs) > 0 else None,
        since=epochs[0].to_pydatetime() if len(epochs) > 0 else None,
        rows=rows,
        caption=panel.caption,
    )
