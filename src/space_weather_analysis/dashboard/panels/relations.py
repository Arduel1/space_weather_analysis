import calendar
from datetime import datetime
from typing import Literal, Self

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from space_weather_analysis.dashboard.labels import Metadata, dataset_title, field_label_with_unit
from space_weather_analysis.dashboard.panels.base import CLOSED, PAYLOAD, PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.panels.series import SourceRef, masked_input
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.util.time_utils import IsoDuration


class BinnedField(BaseModel):
    """A stored field of the dataset of a heatmap, or a part of the time of each value, cut into bins along one axis.

    Attributes:
        field: The data field; empty for a part of the time.
        time_part: Bins the month of the year (1 to 12) or the UTC hour of the day (0 to 23) of each value instead of a
            field; None for a field.
        edges: The bin edges, ascending; a value equal to a lower edge belongs to the bin below it.
        label: The axis title; empty takes the label and unit from the metadata.
        factor: Multiplies the stored values, for fields stored in scaled units.
    """

    model_config = CLOSED

    field: str = ""
    time_part: Literal["month", "hour"] | None = None
    edges: list[float] = Field(min_length=2)
    label: str = ""
    factor: float = 1.0

    @model_validator(mode="after")
    def _ascending(self) -> Self:
        """Rejects edges that are not strictly ascending, and an axis naming both or neither a field and a time part."""
        if any(low >= high for low, high in zip(self.edges, self.edges[1:], strict=False)):
            raise ValueError("Bin edges must be strictly ascending.")
        if bool(self.field) == (self.time_part is not None):
            raise ValueError("A heatmap axis names either a field or a time part.")
        return self

    def values(self, frame: pd.DataFrame, dataset: str, record: Metadata) -> pd.Series:
        """Returns the value of each document along this axis: its valid field value scaled by the factor, or its time part."""
        if self.time_part is not None:
            times = pd.DatetimeIndex(frame.index)
            return pd.Series(times.month if self.time_part == "month" else times.hour, index=frame.index, dtype=float)
        return masked_input(frame, SourceRef(dataset=dataset, field=self.field), record) * self.factor

    def tick_labels(self) -> list[str]:
        """Names each bin: the month for months, the UTC hours for hours, else its range of values."""
        bins = list(zip(self.edges, self.edges[1:], strict=False))
        # A month bin (m - 1, m] holds month m, named by its English abbreviation as Python writes it outside any locale
        if self.time_part == "month":
            return [calendar.month_abbr[int(high)] for _, high in bins]
        if self.time_part == "hour":
            return [f"{int(low):02d}-{int(high):02d} UT" for low, high in bins]
        return [f"{low:g} to {high:g}" for low, high in bins]


class ValueField(BaseModel):
    """The stored field whose typical value a heatmap shows per bin.

    Attributes:
        field: The data field.
        label: The color bar title; empty takes the label and unit from the metadata.
        factor: Multiplies the stored values, for fields stored in scaled units.
    """

    model_config = CLOSED

    field: str = Field(min_length=1)
    label: str = ""
    factor: float = 1.0


class GroupedPanel(PanelConfig):
    """The typical value of one quantity for each bin of another, as bars or as one line per band of a third.

    Simpler to read than a heatmap: each line or bar answers one question, such as how the planetary geomagnetic index
    Kp grows as the north-south component Bz of the interplanetary magnetic field turns south, for slow, medium and fast
    solar wind.

    Attributes:
        dataset: The dataset ID holding all fields, which must belong to a feed.
        history: The span of history the values are combined over, ending with the newest stored document.
        x: The quantity along the horizontal axis, a field or a part of the time.
        bands: The quantity splitting the values into one line per bin; None for a single series.
        band_labels: The name of each band, one per bin of the bands.
        z: The quantity combined per bin.
        statistic: The mean of the values, or the share in percent of the values at or above the threshold.
        threshold: The bound of the share; required for the share.
        style: Bars for a single series, lines for bands.
        y_label: The title of the value axis.
        color: The color of the bars or of the strongest band, a CSS color or a theme color token; weaker bands take
            lighter shades of it in band order.
        band_colors: One color per band, taking precedence over the shades of color, such as warning colors that
            grow with the strength of what the bands lead to.
        caption: What the chart shows and why, with the publications the relationship comes from.
        min_count: The fewest values a bin needs to show a value.
    """

    dataset: str = Field(min_length=1)
    history: IsoDuration
    x: BinnedField
    bands: BinnedField | None = None
    band_labels: list[str] = Field(default_factory=list)
    z: ValueField
    statistic: Literal["mean", "share_above"] = "mean"
    threshold: float | None = None
    style: Literal["bars", "lines"] = "lines"
    y_label: str = Field(min_length=1)
    color: str | None = None
    band_colors: list[str] = Field(default_factory=list)
    caption: str = ""
    min_count: int = Field(default=20, gt=0)

    @model_validator(mode="after")
    def _complete(self) -> Self:
        """Rejects a share without threshold and band labels that do not name every band."""
        if self.statistic == "share_above" and self.threshold is None:
            raise ValueError("A share of values above a bound needs the threshold.")
        if self.bands is not None and len(self.band_labels) != len(self.bands.edges) - 1:
            raise ValueError("Give one band label per bin of the bands.")
        if self.band_colors and len(self.band_colors) != len(self.band_labels or [""]):
            raise ValueError("Give one band color per band.")
        return self

    def shown_fields(self) -> dict[str, list[str]]:
        """Lists the fields of the axes and the combined quantity."""
        axes = [self.x, self.bands] if self.bands is not None else [self.x]
        return {self.dataset: [axis.field for axis in axes if axis.field] + [self.z.field]}


class GroupedSeries(BaseModel):
    """One bar series or line of a grouped chart.

    Attributes:
        label: The name of the band; empty for a single series.
        values: The combined value per bin of x, null where a bin has too few values.
        counts: The number of values per bin of x.
    """

    model_config = PAYLOAD

    label: str
    values: list[float | None]
    counts: list[int]


class GroupedPayload(PanelPayload):
    """The JSON document of a grouped chart.

    Attributes:
        title: The heading.
        x_label: The title of the horizontal axis.
        x_ticks: The name of each bin along x.
        y_label: The title of the value axis.
        style: bars or lines.
        color: The color of the bars or of the last band; None for the palette colors.
        band_colors: One color per band; empty for shades of color.
        series: The series, one per band.
        caption: What the chart shows and why.
        since: The time of the oldest value used.
        values: The number of values used.
    """

    title: str
    x_label: str
    x_ticks: list[str]
    y_label: str
    style: str
    color: str | None
    band_colors: list[str]
    series: list[GroupedSeries]
    caption: str
    since: datetime | None
    values: int


def build_grouped(panel: GroupedPanel, reader: DashboardReader, context: PanelContext) -> GroupedPayload:
    """Builds the JSON document of a grouped chart from the history of its dataset.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export.

    Returns:
        The payload.
    """
    del context
    axes = [panel.x] if panel.bands is None else [panel.x, panel.bands]
    fields = tuple(field for field in [axis.field for axis in axes] + [panel.z.field] if field)
    frame = reader.recent(panel.dataset, panel.history, fields)
    record = reader.metadata(panel.dataset)
    columns = {
        "x": panel.x.values(frame, panel.dataset, record),
        "z": masked_input(frame, SourceRef(dataset=panel.dataset, field=panel.z.field), record) * panel.z.factor,
    }
    if panel.bands is not None:
        columns["band"] = panel.bands.values(frame, panel.dataset, record)
    values = pd.DataFrame(columns).dropna()
    # The position of the bin of each value, NaN outside the edges; a bin includes its upper edge, not its lower one
    values["x_bin"] = pd.cut(values["x"], panel.x.edges, labels=False)
    values["band_bin"] = pd.cut(values["band"], panel.bands.edges, labels=False) if panel.bands is not None else 0
    values = values.dropna(subset=["x_bin", "band_bin"])
    # The mean of 100 for each value at or above the threshold and 0 for the others is the share in percent
    combined = values["z"] if panel.statistic == "mean" else (values["z"] >= (panel.threshold or 0.0)) * 100.0
    grouped = combined.groupby([values["band_bin"], values["x_bin"]])
    means, counts = grouped.mean(), grouped.count()
    labels = panel.band_labels if panel.bands is not None else [""]
    series = []
    # Every bin of x gets an entry, also one without values; a bin with fewer than min_count values shows none
    for band, label in enumerate(labels):
        band_counts = [int(counts.get((band, x_bin), 0)) for x_bin in range(len(panel.x.edges) - 1)]
        band_means = [float(means.get((band, x_bin), np.nan)) for x_bin in range(len(band_counts))]
        band_values = [round(mean, 3) if count >= panel.min_count else None for mean, count in zip(band_means, band_counts, strict=True)]
        series.append(GroupedSeries(label=label, values=band_values, counts=band_counts))
    return GroupedPayload(
        type=panel.type,
        title=panel.title or dataset_title(record),
        # A configured axis title replaces label and unit
        x_label=panel.x.label or field_label_with_unit(record, panel.x.field),
        x_ticks=panel.x.tick_labels(),
        y_label=panel.y_label,
        style=panel.style,
        color=panel.color,
        band_colors=panel.band_colors,
        series=series,
        caption=panel.caption,
        since=values.index.min().to_pydatetime() if not values.empty else None,
        values=len(values),
    )
