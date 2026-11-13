from datetime import datetime

import numpy as np
import pandas as pd
from pydantic import Field

from space_weather_analysis.dashboard.freshness import DatasetFreshness
from space_weather_analysis.dashboard.panels.base import PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.panels.series import SourceRef, read_freshness
from space_weather_analysis.dashboard.panels.timeseries import (
    CYCLE_SEPARATION,
    MarkPayload,
    RowConfig,
    RowPayload,
    cycle_marks,
    load_frames,
    row_payload,
)
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.heliosphere.propagation import SOLAR_RADIUS_KM
from space_weather_analysis.util.time_utils import IsoDuration

# Fields of a daily region entry: the heliographic latitude in degrees and the sunspot area in km2
LATITUDE_FIELD = "latitude"
AREA_FIELD = "area_km2"
# Mean synodic rotation period of the Sun in days, the Carrington rotation, the time step of the butterfly diagram: the
# latitudes of the sunspots over time, which form wing-like bands in each solar cycle. The value is the common rounding
# of the sidereal period of 25.38 days of Carrington, R. C. (1863), Observations of the Spots on the Sun, seen from the
# orbiting Earth; sunpy.sun.constants gives 27.27526 days (Archinal et al. 2018), which would move the columns.
CARRINGTON_ROTATION_DAYS = 27.2753
# Latitude step of the butterfly diagram in degrees
BUTTERFLY_LATITUDE_STEP = 2.0
# Highest latitude north and south the butterfly diagram covers in degrees; sunspots lie closer to the equator
BUTTERFLY_LATITUDE_LIMIT = 60.0
# Lower bounds of the classes of the butterfly diagram: the share of a latitude strip covered by sunspots in percent,
# as in the diagram of Hathaway (NASA MSFC)
BUTTERFLY_CLASSES = (0.0, 0.1, 1.0)
# Publication of the form of the butterfly diagram
BUTTERFLY_REFERENCE = "Hathaway, D. H. (2015), The Solar Cycle, Living Rev. Sol. Phys. 12, 4, doi:10.1007/lrsp-2015-4"


class ButterflyPanel(PanelConfig):
    """The butterfly diagram of the solar cycles, with chart areas below it on the same time axis.

    Each cell of the diagram is one solar rotation and one latitude strip, classed by the share of the strip that sunspots
    cover on average. The minima and maxima of the cycles, found in the smoothed sunspot number, are marked.

    Attributes:
        dataset: The dataset of daily region entries with latitude and area.
        cycle: The smoothed sunspot number the cycle minima and maxima are found in.
        history: The span shown, ending now.
        rows: The chart areas below the diagram, such as the sunspot number.
    """

    dataset: str = Field(min_length=1)
    cycle: SourceRef
    history: IsoDuration
    rows: list[RowConfig] = Field(default_factory=list)

    def shown_fields(self) -> dict[str, list[str]]:
        """Lists the latitude and area of the regions, the smoothed sunspot number and the fields of the rows."""
        shown: dict[str, list[str]] = {self.dataset: [LATITUDE_FIELD, AREA_FIELD], self.cycle.dataset: [self.cycle.field]}
        for row in self.rows:
            for series in row.series:
                for ref in series.stored_fields():
                    if ref.field not in shown.setdefault(ref.dataset, []):
                        shown[ref.dataset].append(ref.field)
        return shown


class ButterflyPayload(PanelPayload):
    """The JSON document of a butterfly diagram with chart areas below it.

    Attributes:
        title: The heading.
        start: The start of the span shown.
        marks: The minima and maxima of the solar cycles in the span.
        rotation_starts: The start of each solar rotation, the columns of the diagram.
        latitude_edges: The edges of the latitude strips, the rows of the diagram, from south to north.
        classes: Per strip and rotation the class of the covered share, 1 for the lowest of BUTTERFLY_CLASSES; null
            without sunspots.
        class_bounds: The lower bounds of the classes in percent of the strip area.
        entries: The number of region entries the diagram is made of.
        rows: The chart areas below the diagram.
        reference: The publication of the form of the diagram.
        freshness: How current the region entries are.
    """

    title: str
    start: datetime
    marks: list[MarkPayload]
    rotation_starts: list[datetime]
    latitude_edges: list[float]
    classes: list[list[int | None]]
    class_bounds: list[float]
    entries: int
    rows: list[RowPayload]
    reference: str
    freshness: list[DatasetFreshness]


def butterfly_classes(
    times: pd.DatetimeIndex, latitudes: np.ndarray, areas: np.ndarray, start: pd.Timestamp, stop: pd.Timestamp
) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray]:
    """Classes the sunspot cover of each solar rotation and latitude strip.

    Args:
        times: The day of each region entry.
        latitudes: The latitude of each entry in degrees.
        areas: The area of each entry in km2; NaN counts as no area.
        start: The start of the first rotation.
        stop: The end of the last rotation.

    Returns:
        The start of each rotation, the latitude edges, and the class per strip (rows) and rotation (columns): 0 without
        sunspots, else 1 plus the number of further bounds of BUTTERFLY_CLASSES the cover reaches; 1 for a cell whose
        regions have no known area.
    """
    rotation = pd.Timedelta(days=CARRINGTON_ROTATION_DAYS)
    count = max(int(np.ceil((stop - start) / rotation)), 1)
    starts = pd.DatetimeIndex([start + rotation * index for index in range(count)])
    edges = np.arange(-BUTTERFLY_LATITUDE_LIMIT, BUTTERFLY_LATITUDE_LIMIT + BUTTERFLY_LATITUDE_STEP, BUTTERFLY_LATITUDE_STEP)
    # Column of each entry by its rotation, row by its latitude strip; entries outside the grid are left out
    columns = np.floor((times - start) / rotation).astype(int)
    rows = np.searchsorted(edges, latitudes, side="right") - 1
    inside = (columns >= 0) & (columns < count) & (rows >= 0) & (rows < len(edges) - 1)
    # Sum of the areas and number of entries per cell; add.at adds every entry, also several in one cell
    total = np.zeros((len(edges) - 1, count))
    np.add.at(total, (rows[inside], columns[inside]), np.nan_to_num(areas[inside]))
    entries = np.zeros((len(edges) - 1, count))
    np.add.at(entries, (rows[inside], columns[inside]), 1)
    # Area of a zone of a sphere between two latitudes: 2 pi R^2 (sin(upper) - sin(lower))
    strip_area = 2 * np.pi * SOLAR_RADIUS_KM**2 * np.diff(np.sin(np.radians(edges)))
    # The cover of a cell is the mean daily sunspot area during the rotation in percent of the strip area
    cover = 100 * total / CARRINGTON_ROTATION_DAYS / strip_area[:, None]
    classes = np.where(total > 0, np.searchsorted(np.asarray(BUTTERFLY_CLASSES), cover, side="right"), 0)
    # Some years of the reports give no area; a cell with regions but no known area has sunspots of unknown cover
    classes = np.where((entries > 0) & (classes == 0), 1, classes)
    return starts, edges, classes


def build_butterfly(panel: ButterflyPanel, reader: DashboardReader, context: PanelContext) -> ButterflyPayload:
    """Builds the JSON document of a butterfly diagram over its span, with its chart areas and the cycle marks.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export.

    Returns:
        The payload.
    """
    start = pd.Timestamp(context.now) - pd.Timedelta(panel.history)
    entries = reader.recent(panel.dataset, panel.history, (LATITUDE_FIELD, AREA_FIELD))
    # The read ends with the newest entry, which may lie before now; the diagram starts one history before now
    if not entries.empty:
        entries = entries[entries.index >= start]
    latitudes = pd.to_numeric(entries.get(LATITUDE_FIELD, pd.Series(dtype=float)), errors="coerce")
    entries = entries[latitudes.notna()]
    areas = pd.to_numeric(entries.get(AREA_FIELD, pd.Series(dtype=float)), errors="coerce").to_numpy(dtype=float)
    starts, edges, classes = butterfly_classes(
        pd.DatetimeIndex(entries.index), latitudes[latitudes.notna()].to_numpy(dtype=float), areas, start, pd.Timestamp(context.now)
    )
    frames = load_frames(panel.rows, panel.history, reader)
    metadata = {dataset: reader.metadata(dataset) for dataset in frames}
    # Cells without sunspots are null in the payload
    cells = [[int(value) if value > 0 else None for value in strip] for strip in classes]
    return ButterflyPayload(
        type=panel.type,
        title=panel.title,
        start=start.to_pydatetime(),
        # Marks are searched from one cycle separation earlier, so that an extreme near the start has its full neighbourhood
        marks=[mark for mark in cycle_marks(panel.cycle, panel.history + CYCLE_SEPARATION, reader) if mark.time >= start],
        rotation_starts=starts.to_pydatetime().tolist(),
        latitude_edges=edges.tolist(),
        classes=cells,
        class_bounds=list(BUTTERFLY_CLASSES),
        entries=len(entries),
        rows=[row_payload(row, frames, metadata) for row in panel.rows],
        reference=BUTTERFLY_REFERENCE,
        freshness=read_freshness([(panel.dataset, entries)], reader.metadata, context),
    )
