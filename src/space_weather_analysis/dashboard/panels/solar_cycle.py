from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from space_weather_analysis.dashboard.freshness import DatasetFreshness
from space_weather_analysis.dashboard.labels import field_label_with_unit
from space_weather_analysis.dashboard.panels.base import CLOSED, PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.panels.events import EventRule, find_events
from space_weather_analysis.dashboard.panels.series import SourceRef, index_times, masked_input, read_freshness
from space_weather_analysis.dashboard.panels.timeseries import MarkPayload, cycle_marks
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.util.time_utils import IsoDuration


class EventPoints(BaseModel):
    """Catalog events drawn as bubbles: a field for their height, one for their size and a flag for their color.

    Attributes:
        dataset: The dataset ID of the catalog.
        y: The field placing each event on the value axis.
        size: The field sizing each bubble.
        flag: A true-or-false field picking the highlighted events.
        flag_label: The name of the highlighted events in the legend.
        other_label: The name of the other events in the legend.
        y_label: The name of the height field; empty takes the label from the metadata.
        size_label: The name of the size field; empty takes the label from the metadata.
    """

    model_config = CLOSED

    dataset: str = Field(min_length=1)
    y: str = Field(min_length=1)
    size: str = Field(min_length=1)
    flag: str = Field(min_length=1)
    flag_label: str = Field(min_length=1)
    other_label: str = Field(min_length=1)
    y_label: str = ""
    size_label: str = ""


class EarthArrivals(BaseModel):
    """A list of what reached Earth, naming for each the times of the catalog events it came from.

    Attributes:
        dataset: The dataset ID of the list, such as the near-Earth ICMEs of Richardson and Cane.
        field: The text field holding the event times, ISO 8601 to the minute, separated by spaces.
        label: The name of the events that reached Earth in the legend.
    """

    model_config = CLOSED

    dataset: str = Field(min_length=1)
    field: str = Field(min_length=1)
    label: str = Field(min_length=1)


# Longest time between a catalog event and the time a list of arrivals names for it, as both round the same first
# coronagraph sighting
ARRIVAL_MATCH = timedelta(minutes=1)
# Groups of the catalog events, which pick their color: other events, highlighted events, events that reached Earth
OTHER_GROUP = 0
FLAGGED_GROUP = 1
REACHED_EARTH_GROUP = 2


class SolarCyclePanel(PanelConfig):
    """The solar cycle over decades with what it drives: every catalog event as a bubble and every detected storm.

    The rows share the time axis instead of a second value axis, so that each quantity keeps its own scale.

    Attributes:
        sunspots: The monthly sunspot number.
        smoothed: The smoothed sunspot number, whose minima and maxima mark the cycles.
        events: The catalog events drawn as bubbles, such as the CMEs.
        earth: The list naming the catalog events that reached Earth, drawn in their own color; None for none.
        storms: What makes a storm, found in the data, such as the Dst (disturbance storm time) index below -100 nT.
        labelled: The number of strongest storms written with their date.
        sunspot_label: The name of the sunspot number; empty takes the label from the metadata.
        history: The span shown, ending with the newest stored value.
    """

    sunspots: SourceRef
    smoothed: SourceRef
    events: EventPoints
    earth: EarthArrivals | None = None
    storms: EventRule
    labelled: int = Field(default=5, ge=0)
    sunspot_label: str = ""
    history: IsoDuration

    def shown_fields(self) -> dict[str, list[str]]:
        """Lists the sunspot fields, the bubble fields and the storm field as the shown fields."""
        shown: dict[str, list[str]] = {}
        for dataset, field in (
            (self.sunspots.dataset, self.sunspots.field),
            (self.smoothed.dataset, self.smoothed.field),
            (self.events.dataset, self.events.y),
            (self.events.dataset, self.events.size),
            (self.events.dataset, self.events.flag),
            (self.storms.dataset, self.storms.field),
            *([(self.earth.dataset, self.earth.field)] if self.earth is not None else []),
        ):
            if field not in shown.setdefault(dataset, []):
                shown[dataset].append(field)
        return shown


class SolarCyclePayload(PanelPayload):
    """The JSON document of the solar cycle view.

    Attributes:
        title: The heading.
        sunspot_label: The name of the sunspot number.
        sunspot_times: The UTC times of the monthly sunspot numbers.
        sunspots: The monthly sunspot numbers.
        smoothed_times: The UTC times of the smoothed sunspot numbers.
        smoothed: The smoothed sunspot numbers.
        marks: The minima and maxima of the cycles found in the smoothed numbers.
        event_label: The name of the bubble height with its unit.
        size_label: The name of the bubble size with its unit.
        flag_label: The name of the highlighted events.
        earth_label: The name of the events that reached Earth; empty without such a list.
        earth_until: The time of the newest arrival of the list, after which no event can be marked; None without list.
        other_label: The name of the other events.
        event_times: The UTC times of the events.
        event_values: The height of each event.
        event_sizes: The size value of each event.
        event_groups: The group of each event: 0 other, 1 highlighted, 2 reached Earth.
        storm_label: The name of the storm field with its unit.
        storm_threshold: The value a storm passes.
        storm_times: The UTC time of the extreme of each storm.
        storm_values: The extreme value of each storm.
        labelled: The positions of the strongest storms in the storm lists.
        freshness: How current each dataset is.
    """

    title: str
    sunspot_label: str
    sunspot_times: list[datetime]
    sunspots: list[float]
    smoothed_times: list[datetime]
    smoothed: list[float]
    marks: list[MarkPayload]
    event_label: str
    size_label: str
    flag_label: str
    earth_label: str
    earth_until: datetime | None
    other_label: str
    event_times: list[datetime]
    event_values: list[float]
    event_sizes: list[float]
    event_groups: list[int]
    storm_label: str
    storm_threshold: float
    storm_times: list[datetime]
    storm_values: list[float]
    labelled: list[int]
    freshness: list[DatasetFreshness]


def _valid(reader: DashboardReader, ref: SourceRef, history: timedelta) -> pd.Series:
    """Reads the valid values of a stored field over a history."""
    frame = reader.recent(ref.dataset, history, (ref.field,))
    return masked_input(frame, ref, reader.metadata(ref.dataset)).dropna()


def _reached_earth(reader: DashboardReader, earth: EarthArrivals, history: timedelta, times: pd.DatetimeIndex) -> tuple[pd.Series, datetime | None]:
    """Tells which catalog event times a list of arrivals names, within ARRIVAL_MATCH, and when the list ends.

    Args:
        reader: The source of documents.
        earth: The list of arrivals.
        history: The span read.
        times: The times of the catalog events.

    Returns:
        A boolean per catalog event, and the time of the newest arrival; None without arrivals.
    """
    frame = reader.recent(earth.dataset, history, (earth.field,))
    if frame.empty or earth.field not in frame.columns:
        return pd.Series(False, index=times), None
    # Every arrival names its catalog events in one text; all texts together give the named times
    texts = " ".join(text for text in frame[earth.field] if isinstance(text, str))
    named = pd.DatetimeIndex(pd.to_datetime(texts.split(), utc=True, errors="coerce").dropna()).unique().sort_values()
    minutes = pd.DatetimeIndex(times).floor("min")
    if named.empty:
        return pd.Series(False, index=times), pd.DatetimeIndex(frame.index).max().to_pydatetime()
    # The nearest named time of each event decides, so that one rounding minute apart still matches
    nearest = named.get_indexer(minutes, method="nearest")
    distance = abs(named[nearest] - minutes)
    reached = pd.Series(distance <= pd.Timedelta(ARRIVAL_MATCH), index=times)
    return reached, pd.DatetimeIndex(frame.index).max().to_pydatetime()


def build_solar_cycle(panel: SolarCyclePanel, reader: DashboardReader, context: PanelContext) -> SolarCyclePayload:
    """Builds the JSON document of the solar cycle view.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export, with the feed interval of each dataset.

    Returns:
        The payload.
    """
    sunspots = _valid(reader, panel.sunspots, panel.history)
    smoothed = _valid(reader, panel.smoothed, panel.history)
    events_ref = panel.events
    frame = reader.recent(events_ref.dataset, panel.history, (events_ref.y, events_ref.size, events_ref.flag))
    record = reader.metadata(events_ref.dataset)
    heights = masked_input(frame, SourceRef(dataset=events_ref.dataset, field=events_ref.y), record)
    sizes = masked_input(frame, SourceRef(dataset=events_ref.dataset, field=events_ref.size), record)
    # Only events with a positive height and a size are drawn, as the height axis is logarithmic
    drawn = (heights > 0) & sizes.notna()
    flags = frame[events_ref.flag].eq(True) if events_ref.flag in frame.columns else pd.Series(False, index=frame.index)
    reached, earth_until = (
        _reached_earth(reader, panel.earth, panel.history, pd.DatetimeIndex(frame.index))
        if panel.earth is not None
        else (pd.Series(False, index=frame.index), None)
    )
    # Reaching Earth outranks the highlight flag for the color of an event
    groups = pd.Series(np.where(reached.to_numpy(), REACHED_EARTH_GROUP, np.where(flags.to_numpy(), FLAGGED_GROUP, OTHER_GROUP)), index=frame.index)
    storm_values = _valid(reader, panel.storms, panel.history) * panel.storms.factor
    storms = find_events(storm_values, panel.storms.threshold, panel.storms.below, panel.storms.separation)
    extremes = storms.to_numpy(dtype=float)
    # Positions of the storms from the strongest on: the lowest values for storms below the threshold, else the highest
    order = np.argsort(extremes) if panel.storms.below else np.argsort(-extremes)
    read: list[tuple[str, pd.Series | pd.DataFrame]] = [
        (panel.sunspots.dataset, sunspots),
        (events_ref.dataset, frame),
        (panel.storms.dataset, storm_values),
    ]
    return SolarCyclePayload(
        type=panel.type,
        title=panel.title,
        sunspot_label=field_label_with_unit(reader.metadata(panel.sunspots.dataset), panel.sunspots.field, panel.sunspot_label),
        sunspot_times=index_times(sunspots),
        sunspots=sunspots.round(1).tolist(),
        smoothed_times=index_times(smoothed),
        smoothed=smoothed.round(1).tolist(),
        marks=cycle_marks(panel.smoothed, panel.history, reader),
        event_label=field_label_with_unit(record, events_ref.y, events_ref.y_label),
        size_label=field_label_with_unit(record, events_ref.size, events_ref.size_label),
        flag_label=events_ref.flag_label,
        earth_label=panel.earth.label if panel.earth is not None else "",
        earth_until=earth_until,
        other_label=events_ref.other_label,
        event_times=index_times(heights[drawn]),
        event_values=[float(f"{value:.3g}") for value in heights[drawn]],
        event_sizes=sizes[drawn].round(0).tolist(),
        event_groups=[int(group) for group in groups[drawn]],
        storm_label=field_label_with_unit(reader.metadata(panel.storms.dataset), panel.storms.field),
        storm_threshold=panel.storms.threshold,
        storm_times=index_times(storms),
        storm_values=storms.round(1).tolist(),
        labelled=sorted(int(position) for position in order[: panel.labelled]),
        freshness=read_freshness(read, reader.metadata, context),
    )
