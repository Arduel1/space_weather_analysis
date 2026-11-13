from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Annotated, Any, Self

import pandas as pd
from pydantic import BaseModel, Field, model_validator

from space_weather_analysis.dashboard.derived import DERIVED
from space_weather_analysis.dashboard.freshness import DatasetFreshness, dataset_freshness
from space_weather_analysis.dashboard.labels import Metadata, dataset_title, field_description, field_label, field_unit
from space_weather_analysis.dashboard.panels.base import (
    PAYLOAD,
    PanelConfig,
    PanelContext,
    PanelPayload,
    ScaleChoice,
    ValidityChoice,
    ValueFormat,
    finite_number,
)
from space_weather_analysis.dashboard.panels.series import SourceRef, json_points, masked_input, reduce_points
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.dashboard.scales import FORMATTERS, NOT_AVAILABLE, Level, format_value
from space_weather_analysis.dashboard.validity import describe_validity, valid_range
from space_weather_analysis.util.time_utils import IsoDuration, SecondsDuration

# Most values of the small chart of a tile
SPARKLINE_POINTS = 300
# Quantiles of the baseline history that bound its usual range
USUAL_LOW_QUANTILE = 0.05
USUAL_HIGH_QUANTILE = 0.95


class ValuePanel(PanelConfig, ScaleChoice, ValueFormat, ValidityChoice):
    """A tile with the newest value of one field and, with a scale, its level; a hidden invalid value shows as "invalid".

    Attributes:
        dataset: The dataset ID, which must belong to a feed.
        field: The data field.
        baseline: A stored field holding the same quantity over a history, which the newest value is ranked in; None for no ranking.
        baseline_history: The span of the baseline history, chosen so that the ranking means something now, such as the
            past year within the same phase of the solar cycle.
        baseline_factor: Multiplies the baseline values, for fields stored in scaled units.
        sparkline: The span of the small chart of recent values; None for none.
        re_express: Names of derived quantities of one input computed from the newest value, such as travel times.
        color: The color of the small chart, a CSS color or a theme color token such as --theme-wind; None takes the
            first palette color.
    """

    dataset: str = Field(min_length=1)
    field: str = Field(min_length=1)
    baseline: SourceRef | None = None
    baseline_history: IsoDuration = timedelta(days=365)
    baseline_factor: float = 1.0
    sparkline: IsoDuration | None = None
    re_express: list[str] = Field(default_factory=list)
    color: str | None = None

    @model_validator(mode="after")
    def _known_re_expressions(self) -> Self:
        """Rejects a re-expression that is no derived quantity of one input."""
        for name in self.re_express:
            if name not in DERIVED or len(DERIVED[name].inputs) != 1:
                raise ValueError(f"Re-expression '{name}' is no derived quantity of one input.")
        return self


@dataclass(frozen=True)
class ValueView:
    """What a value tile shows.

    Attributes:
        title: The heading: the configured title, else the label of the field.
        text: The formatted value with unit, "n/a" without a value.
        time: The UTC time stamp of the value; None without a value.
        level: The level of the value; None without scale, value or level.
        help_lines: The description of the field, the title of its dataset and its valid values, each where known.
        measured: Whether the tile shows a valid measured value, which is then ranked and re-expressed; left out of
            the payload.
    """

    title: str
    text: str
    time: datetime | None
    level: Level | None
    help_lines: tuple[str, ...] = ()
    measured: Annotated[bool, Field(exclude=True)] = False


def value_view(panel: ValuePanel, latest: tuple[datetime, dict[str, Any]] | None, metadata: Metadata) -> ValueView:
    """Builds what a value tile shows from the newest document of its dataset and the metadata record of the dataset.

    Args:
        panel: The panel settings.
        latest: The time stamp and data fields of the newest document; None if there is none.
        metadata: The metadata record of the dataset; empty if it has none.

    Returns:
        The view; a missing, non-numeric or non-finite value shows "n/a" without level.
    """
    # Configured title and unit take precedence over the metadata, which takes precedence over the field name
    title = panel.title or field_label(metadata, panel.field)
    empty = ValueView(title=title, text=NOT_AVAILABLE, time=None, level=None, help_lines=tuple(_help_lines(panel, metadata)))
    if latest is None:
        return empty
    time, fields = latest
    value = finite_number(fields.get(panel.field))
    if value is None:
        return replace(empty, time=time)
    if panel.hide_invalid and not valid_range(metadata, panel.field, panel.valid_min, panel.valid_max).is_valid(value):
        return replace(empty, text="invalid", time=time)
    # A named formatter writes a complete text such as a flare class, which takes no unit
    unit = "" if panel.format in FORMATTERS else field_unit(metadata, panel.field, panel.unit)
    scale = panel.resolved_scale()
    level = scale.level_of(value) if scale is not None else None
    text = format_value(value, panel.format)
    # A named formatter writes NOT_AVAILABLE for a value it cannot name, such as a non-positive flux for the flare class
    return replace(empty, text=f"{text} {unit}".rstrip(), time=time, level=level, measured=text != NOT_AVAILABLE)


def _help_lines(panel: ValuePanel, metadata: Metadata) -> list[str]:
    """Lists the lines of the tile help: field description, dataset title and, where known, the valid values."""
    lines = []
    description = field_description(metadata, panel.field)
    if description:
        lines.append(description)
    title = dataset_title(metadata)
    if title:
        lines.append(f"Dataset: {title}")
    if valid_range(metadata, panel.field, panel.valid_min, panel.valid_max).is_known:
        lines.append(f"Valid values: {describe_validity(metadata, panel.field, panel.valid_min, panel.valid_max)}")
    return lines


class ContextPayload(BaseModel):
    """Where the newest value stands within the history of the same quantity.

    Attributes:
        percentile: The share of historical values at or below the newest value, in percent.
        median: The median of the historical values.
        low: The 5 % quantile of the historical values.
        high: The 95 % quantile of the historical values.
        since: The time of the oldest historical value.
        span: The configured span of the history, which the frontend names, such as the past 12 months.
        values: The number of historical values.
        dataset: The dataset ID of the history.
    """

    model_config = PAYLOAD

    percentile: float
    median: float
    low: float
    high: float
    since: datetime
    span: SecondsDuration
    values: int
    dataset: str


class SparklinePayload(BaseModel):
    """The recent values of a tile.

    Attributes:
        times: The UTC time stamps.
        values: The values, null where a value is missing.
    """

    model_config = PAYLOAD

    times: list[datetime]
    values: list[float | None]


class ReExpressionPayload(BaseModel):
    """A quantity computed from the newest value with a published formula or a constant.

    Attributes:
        label: The name of the quantity.
        text: The formatted value with unit.
        reference: Where the formula or constant comes from.
        formula: The formula.
    """

    model_config = PAYLOAD

    label: str
    text: str
    reference: str
    formula: str


class ValuePayload(PanelPayload):
    """The JSON document of a value tile.

    Attributes:
        view: What the tile shows.
        freshness: How current the dataset is; empty without a stored value.
        context: Where the newest value stands in the baseline history; None without baseline or values.
        sparkline: The recent values; None without sparkline.
        re_expressions: The quantities computed from the newest value.
        color: The color of the small chart; None for the first palette color.
    """

    view: ValueView
    freshness: list[DatasetFreshness]
    context: ContextPayload | None = None
    sparkline: SparklinePayload | None = None
    re_expressions: list[ReExpressionPayload] = Field(default_factory=list)
    color: str | None = None


def _context(panel: ValuePanel, value: float, reader: DashboardReader) -> ContextPayload | None:
    """Ranks the newest value within the baseline history, leaving out fill values and values outside the valid range."""
    if panel.baseline is None:
        return None
    frame = reader.recent(panel.baseline.dataset, panel.baseline_history, (panel.baseline.field,))
    history = (masked_input(frame, panel.baseline, reader.metadata(panel.baseline.dataset)) * panel.baseline_factor).dropna()
    if history.empty:
        return None
    # The percentile is the share of the history at or below the value; the quantiles bound the usual range
    return ContextPayload(
        percentile=float((history <= value).mean() * 100),
        median=float(history.median()),
        low=float(history.quantile(USUAL_LOW_QUANTILE)),
        high=float(history.quantile(USUAL_HIGH_QUANTILE)),
        since=history.index.min().to_pydatetime(),
        span=panel.baseline_history,
        values=len(history),
        dataset=panel.baseline.dataset,
    )


def _sparkline(panel: ValuePanel, reader: DashboardReader) -> SparklinePayload | None:
    """Reads the recent values of the tile field."""
    if panel.sparkline is None:
        return None
    frame = reader.recent(panel.dataset, panel.sparkline, (panel.field,))
    if frame.empty or panel.field not in frame.columns:
        return None
    times, values = json_points(reduce_points(pd.to_numeric(frame[panel.field], errors="coerce"), max_points=SPARKLINE_POINTS))
    return SparklinePayload(times=times, values=values)


def _re_expressions(panel: ValuePanel, value: float) -> list[ReExpressionPayload]:
    """Computes the re-expressions of the newest value."""
    expressions = []
    for name in panel.re_express:
        quantity = DERIVED[name]
        # The formulas work on series, so the single value goes in as a series of one
        result = quantity.compute(pd.Series([value], dtype=float)).iloc[0]
        # A formula without result for this value, such as a travel time at zero speed, is left out
        if pd.notna(result):
            expressions.append(
                ReExpressionPayload(
                    label=quantity.label, text=f"{result:.3g} {quantity.units}".strip(), reference=quantity.reference, formula=quantity.formula
                )
            )
    return expressions


def build_value(panel: ValuePanel, reader: DashboardReader, context: PanelContext) -> ValuePayload:
    """Builds the JSON document of a value tile from the newest document and the metadata of its dataset.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export, with the feed interval of each dataset.

    Returns:
        The payload.
    """
    metadata = reader.metadata(panel.dataset)
    latest = reader.latest(panel.dataset, (panel.field,))
    view = value_view(panel, latest, metadata)
    freshness = [] if view.time is None else [dataset_freshness(panel.dataset, view.time, metadata, context.feed_intervals.get(panel.dataset))]
    # Only a value the tile shows as measurement is ranked and re-expressed
    value = finite_number(latest[1].get(panel.field)) if latest is not None and view.measured else None
    return ValuePayload(
        type=panel.type,
        view=view,
        freshness=freshness,
        context=_context(panel, value, reader) if value is not None else None,
        sparkline=_sparkline(panel, reader),
        re_expressions=_re_expressions(panel, value) if value is not None else [],
        color=panel.color,
    )
