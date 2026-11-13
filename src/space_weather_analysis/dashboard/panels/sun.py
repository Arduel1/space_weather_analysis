import json
from datetime import datetime, timedelta
from typing import TypeGuard

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from space_weather_analysis.clients.image_sequences import CORONAL_HOLES_FIELD, DISK_FIELDS, FRAME_FILE_FIELD
from space_weather_analysis.dashboard.freshness import DatasetFreshness, dataset_freshness
from space_weather_analysis.dashboard.panels.base import CLOSED, PAYLOAD, PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.panels.images import frame_url, media_file
from space_weather_analysis.dashboard.panels.series import read_freshness
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.solar.coronal_holes import CORONAL_HOLE_REFERENCE, CoronalHole
from space_weather_analysis.solar.geometry import MEEUS_REFERENCE, ROTATION_REFERENCE, disk_position, location_coordinates, solar_b0, synodic_rotation
from space_weather_analysis.util.time_utils import IsoDuration

# Fields of a daily region report: the NOAA region number, the place on the Sun such as S08E35, the Carrington longitude,
# the area in millionths of the solar hemisphere, the McIntosh class and the Mount Wilson class
REGION_NUMBER_FIELD = "region"
REGION_LOCATION_FIELD = "location"
CARRINGTON_LONGITUDE_FIELD = "carrington_longitude"
REGION_AREA_FIELD = "area"
SPOT_CLASS_FIELD = "spot_class"
MAGNETIC_CLASS_FIELD = "mag_class"
# Fields of a region report counting the C, M and X class X-ray flares of the region on the day of the report
FLARE_COUNT_FIELDS = ("c_xray_events", "m_xray_events", "x_xray_events")
# Fields of a region report the panels read
REGION_FIELDS = (
    REGION_NUMBER_FIELD,
    REGION_LOCATION_FIELD,
    CARRINGTON_LONGITUDE_FIELD,
    REGION_AREA_FIELD,
    SPOT_CLASS_FIELD,
    MAGNETIC_CLASS_FIELD,
    *FLARE_COUNT_FIELDS,
)
# Columns region_reports adds to the reports: the heliographic latitude and the longitude from the central meridian in
# degrees, west positive
REGION_LATITUDE_COLUMN = "latitude"
REGION_LONGITUDE_COLUMN = "longitude"
# Letters of the Mount Wilson class codes of NOAA, which class the magnetic complexity of a sunspot group, such as BGD for
# beta-gamma-delta
MAGNETIC_CLASS_LETTERS = {"A": "alpha", "B": "beta", "G": "gamma", "D": "delta"}
# Name of the class of a region the report gives no magnetic class for
UNCLASSIFIED = "not classified"
# Farthest a report may lie from a frame in time to place a region on it; farther ones belong to regions out of view
REPORT_MAX_AGE = timedelta(days=2)
# Time after the day of a report at which its locations are valid: the report of a day holds the locations at the end of
# that day, as the Solar Region Summary text states (Locations Valid at {day}/2400Z) for the same locations
REPORT_VALID_AFTER_DAY = timedelta(days=1)


def magnetic_class_name(code: object) -> str:
    """Spells a Mount Wilson class code of NOAA such as BGD as beta-gamma-delta; the code itself if it is of another form."""
    # A missing code is unclassified; a code with a letter of no Mount Wilson class stays as written
    if not isinstance(code, str) or not code or any(letter not in MAGNETIC_CLASS_LETTERS for letter in code.upper()):
        return str(code) if isinstance(code, str) and code else UNCLASSIFIED
    return "-".join(MAGNETIC_CLASS_LETTERS[letter] for letter in code.upper())


def region_reports(reader: DashboardReader, dataset: str, history: timedelta) -> pd.DataFrame:
    """Reads the region reports of a history with their position from the location text.

    Args:
        reader: The source of documents.
        dataset: The dataset of the daily region reports.
        history: The span of reports read, ending with the newest stored report.

    Returns:
        One row per region and day with a UTC DatetimeIndex, the REGION_FIELDS and latitude and longitude; rows without
        readable location are left out.
    """
    frame = reader.recent(dataset, history, REGION_FIELDS)
    if frame.empty or REGION_LOCATION_FIELD not in frame.columns:
        return pd.DataFrame(columns=[*REGION_FIELDS, REGION_LATITUDE_COLUMN, REGION_LONGITUDE_COLUMN])
    # Each location text such as S08E35 becomes a pair of latitude and longitude, None if it cannot be read
    coordinates = frame[REGION_LOCATION_FIELD].map(location_coordinates)
    frame = frame[coordinates.notna()].copy()
    # The reshape keeps two columns also when no row is left
    pairs = np.array(coordinates.dropna().tolist(), dtype=float).reshape(-1, 2)
    frame[REGION_LATITUDE_COLUMN], frame[REGION_LONGITUDE_COLUMN] = pairs[:, 0], pairs[:, 1]
    return frame


class SunDiskView(BaseModel):
    """One image sequence of the whole Sun shown in a sun disk panel.

    Attributes:
        dataset: The dataset of the frames, with the disk position stored with each frame.
        label: The name written on the view.
    """

    model_config = CLOSED

    dataset: str = Field(min_length=1)
    label: str = Field(min_length=1)


class SunDiskPanel(PanelConfig):
    """Image sequences of the whole Sun side by side, played together, with each numbered sunspot region drawn at its place.

    The region positions of the daily reports move with the differential rotation of the Sun to the time of each frame.

    Attributes:
        views: The image sequences, side by side.
        regions: The dataset of the daily region reports with location, area, classes and flares.
        history: The span of frames shown, ending with the newest stored frame.
    """

    views: list[SunDiskView] = Field(min_length=1)
    regions: str = Field(min_length=1)
    history: IsoDuration

    def shown_fields(self) -> dict[str, list[str]]:
        """Lists the frame files and the region fields as the shown fields."""
        shown: dict[str, list[str]] = {view.dataset: [FRAME_FILE_FIELD] for view in self.views}
        shown[self.regions] = [REGION_NUMBER_FIELD, REGION_LOCATION_FIELD, REGION_AREA_FIELD, SPOT_CLASS_FIELD, MAGNETIC_CLASS_FIELD]
        return shown


class SunRegion(BaseModel):
    """One sunspot region of the sun disk view, as its newest report describes it.

    Attributes:
        region: The NOAA region number.
        area: The area in millionths of the solar hemisphere.
        spot_class: The McIntosh class of the sunspot group, which classes its size, penumbra and spot distribution.
        magnetic_class: The Mount Wilson class spelled out, such as beta-gamma-delta.
        flares: The number of C, M and X class flares of the region in the reports shown.
    """

    model_config = PAYLOAD

    region: int
    area: float | None
    spot_class: str | None
    magnetic_class: str
    flares: tuple[int, int, int]


class SunDiskFrame(BaseModel):
    """One frame of the sun disk view.

    Attributes:
        time: The time of the image.
        url: The path of the image file relative to the data directory of the site.
        markers: The visible regions as position in the region list, x and y as shares of the image width and height.
        disk: The disk centre x and y as shares of the image width and height and its radius as share of the width.
        coronal_holes: The outlines of the coronal holes found in the frame, as x and y shares of the image size.
    """

    model_config = PAYLOAD

    time: datetime
    url: str
    markers: list[tuple[int, float, float]]
    disk: tuple[float, float, float]
    coronal_holes: list[list[tuple[float, float]]] = Field(default_factory=list)


class SunDiskViewPayload(BaseModel):
    """One image sequence of the sun disk view.

    Attributes:
        label: The name written on the view.
        dataset: The dataset of the frames, which names the directory of their files.
        frames: The frames in time order.
    """

    model_config = PAYLOAD

    label: str
    dataset: str
    frames: list[SunDiskFrame]


class SunDiskPayload(PanelPayload):
    """The JSON document of the sun disk view.

    Attributes:
        title: The heading.
        regions: The regions drawn on any frame.
        views: The image sequences side by side.
        reference: The publications of the tilt of the solar axis and of the rotation the positions are computed with.
        formula: The rotation the positions move with.
        freshness: How current the frames and reports are.
    """

    title: str
    regions: list[SunRegion]
    views: list[SunDiskViewPayload]
    reference: str
    formula: str
    freshness: list[DatasetFreshness]

    def media_files(self) -> list[tuple[str, str]]:
        """Lists the frame files the payload refers to as pairs of dataset ID and file name."""
        return [media_file(frame.url) for view in self.views for frame in view.frames]


def parse_coronal_holes(text: object) -> list[CoronalHole]:
    """Reads the coronal holes a frame stores as JSON text; none for a frame without them."""
    if not isinstance(text, str) or not text:
        return []
    return [CoronalHole.model_validate(hole) for hole in json.loads(text)]


def _is_number(value: object) -> TypeGuard[int | float | np.integer | np.floating]:
    """Tells whether a stored value is a present number; numpy numbers count as numbers, NaN as missing."""
    return isinstance(value, int | float | np.integer | np.floating) and not np.isnan(value)


def _count(value: object) -> int:
    """Reads a count that may be missing as a whole number, 0 when missing."""
    return int(value) if _is_number(value) else 0


def _view_frames(dataset: str, history: timedelta, reports: pd.DataFrame, position_of: dict[int, int], reader: DashboardReader) -> list[SunDiskFrame]:
    """Builds the frames of one image sequence with the visible regions placed on each.

    The disk of a sequence is the median of the disks of its frames: the disk barely changes within a day, and the median
    keeps a single badly measured frame from moving the markers.

    Args:
        dataset: The dataset of the frames.
        history: The span of frames, ending with the newest stored frame.
        reports: The region reports with latitude and longitude.
        position_of: The position of each region number in the region list of the payload.
        reader: The source of documents.

    Returns:
        The frames in time order; none without a frame of known disk.
    """
    images = reader.recent(dataset, history, (FRAME_FILE_FIELD, *DISK_FIELDS, CORONAL_HOLES_FIELD))
    # Frames without known disk cannot carry markers; without any such frame the view stays empty
    images = images.reindex(columns=[FRAME_FILE_FIELD, *DISK_FIELDS, CORONAL_HOLES_FIELD]).dropna(subset=[FRAME_FILE_FIELD, *DISK_FIELDS])
    if images.empty:
        return []
    disk_x, disk_y, radius = (float(images[field].astype(float).median()) for field in DISK_FIELDS)
    times = pd.DatetimeIndex(images.index)
    # B0, the tilt of the solar axis towards Earth at each frame time, decides how latitudes project onto the disk
    tilts = solar_b0(times)
    # Nanoseconds since 1970 UTC, the unit of the frame times
    valid_nanoseconds = (pd.DatetimeIndex(reports.index) + REPORT_VALID_AFTER_DAY).as_unit("ns").to_numpy(dtype=np.int64)
    frames = []
    for time, file_name, holes_text, tilt in zip(times, images[FRAME_FILE_FIELD], images[CORONAL_HOLES_FIELD], tilts, strict=True):
        # Each region is placed from its report valid closest to the frame, moved forward or back by the rotation; the
        # offsets of the reports from the frame in days
        offsets = (time.value - valid_nanoseconds) / pd.Timedelta(days=1).value
        # A plain position index, as many reports share one day and idxmin must name single rows
        near = reports.assign(days=offsets).reset_index(drop=True)
        near = near[near["days"].abs() <= REPORT_MAX_AGE / timedelta(days=1)]
        latest = near.loc[near["days"].abs().groupby(near[REGION_NUMBER_FIELD]).idxmin()] if not near.empty else near
        # The synodic rotation rate depends on the latitude, in degrees per day as seen from Earth
        latitude = latest[REGION_LATITUDE_COLUMN].to_numpy(dtype=float)
        longitude = latest[REGION_LONGITUDE_COLUMN].to_numpy(dtype=float) + synodic_rotation(latitude) * latest["days"].to_numpy(dtype=float)
        # Disk coordinates in solar radii from the disk centre; regions on the far side are not visible
        west, north, visible = disk_position(latitude, longitude, np.full(len(latitude), tilt))
        # Images of the whole Sun are square, so the radius share of the width also holds for the height
        markers = [
            (position_of[int(number)], round(disk_x + x * radius, 4), round(disk_y - y * radius, 4))
            for number, x, y, shown in zip(latest[REGION_NUMBER_FIELD], west, north, visible, strict=True)
            if shown
        ]
        frames.append(
            SunDiskFrame(
                time=time.to_pydatetime(),
                url=frame_url(dataset, str(file_name)),
                markers=markers,
                disk=(disk_x, disk_y, radius),
                coronal_holes=[hole.outline for hole in parse_coronal_holes(holes_text)],
            )
        )
    return frames


def build_sun_disk(panel: SunDiskPanel, reader: DashboardReader, context: PanelContext) -> SunDiskPayload:
    """Builds the JSON document of the sun disk view from the frames of its views and the region reports.

    Each frame shows each region at the position of its report valid closest to the frame, moved by the synodic rotation at
    its latitude; positions on the far side are left out.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export.

    Returns:
        The payload.
    """
    reports = region_reports(reader, panel.regions, panel.history + REPORT_MAX_AGE)
    regions: list[SunRegion] = []
    position_of: dict[int, int] = {}
    for number, rows in reports.groupby(REGION_NUMBER_FIELD):
        # The reports come sorted by time, so the last row of a region is its newest report
        newest = rows.iloc[-1]
        region_number = int(str(number))
        position_of[region_number] = len(regions)
        # The flares of a region over all its daily reports in the span, per class C, M and X
        flares = tuple(_count(rows[field].sum()) for field in FLARE_COUNT_FIELDS)
        area = newest.get(REGION_AREA_FIELD)
        regions.append(
            SunRegion(
                region=region_number,
                area=float(area) if _is_number(area) else None,
                spot_class=newest.get(SPOT_CLASS_FIELD) if isinstance(newest.get(SPOT_CLASS_FIELD), str) else None,
                magnetic_class=magnetic_class_name(newest.get(MAGNETIC_CLASS_FIELD)),
                flares=(flares[0], flares[1], flares[2]),
            )
        )
    views = [
        SunDiskViewPayload(label=view.label, dataset=view.dataset, frames=_view_frames(view.dataset, panel.history, reports, position_of, reader))
        for view in panel.views
    ]
    freshness = [
        dataset_freshness(view.dataset, view.frames[-1].time, reader.metadata(view.dataset), context.feed_intervals.get(view.dataset))
        for view in views
        if view.frames
    ]
    freshness += read_freshness([(panel.regions, reports)], reader.metadata, context)
    # The publication of the coronal hole detection is cited only when a frame shows coronal holes
    holes = any(frame.coronal_holes for view in views for frame in view.frames)
    return SunDiskPayload(
        type=panel.type,
        title=panel.title,
        regions=regions,
        views=views,
        reference=f"{MEEUS_REFERENCE}; {ROTATION_REFERENCE}" + (f"; {CORONAL_HOLE_REFERENCE}" if holes else ""),
        formula="omega = 14.713 - 2.396 sin^2(B) - 1.787 sin^4(B) - 0.9856 degrees per day at latitude B",
        freshness=freshness,
    )
