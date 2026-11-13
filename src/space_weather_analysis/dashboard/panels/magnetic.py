from datetime import datetime, timedelta

import astropy.units as u
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sunpy.coordinates import sun

from space_weather_analysis.clients.donki import EVENT_TYPE_FIELD, LABEL_FIELD, LOCATION_FIELD, DonkiEventType
from space_weather_analysis.clients.image_sequences import CORONAL_HOLES_FIELD, DISK_FIELDS, FRAME_FILE_FIELD
from space_weather_analysis.dashboard.freshness import DatasetFreshness, dataset_freshness
from space_weather_analysis.dashboard.panels.base import PAYLOAD, PanelConfig, PanelContext, PanelPayload, text_or_empty
from space_weather_analysis.dashboard.panels.images import frame_url, media_file
from space_weather_analysis.dashboard.panels.sun import (
    CARRINGTON_LONGITUDE_FIELD,
    MAGNETIC_CLASS_FIELD,
    REGION_AREA_FIELD,
    REGION_LATITUDE_COLUMN,
    REGION_LOCATION_FIELD,
    REGION_NUMBER_FIELD,
    magnetic_class_name,
    parse_coronal_holes,
    region_reports,
)
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.solar.geometry import heliographic_position, location_coordinates, solar_b0
from space_weather_analysis.solar.magnetic_field import PFSS_REFERENCE
from space_weather_analysis.util.time_utils import IsoDuration

# Span searched for the newest daily region report
REGION_REPORT_SPAN = timedelta(days=3)


def _carrington_longitude(centre_longitude: float, longitude: float) -> float:
    """Turns a longitude from the central meridian into a Carrington longitude in degrees, rounded to 0.01 deg.

    Args:
        centre_longitude: The Carrington longitude of the disk centre seen from Earth in degrees.
        longitude: The longitude from the central meridian in degrees, west positive.

    Returns:
        The Carrington longitude from 0 to below 360 degrees.
    """
    # Carrington longitudes are fixed to the rotating Sun, so a place keeps its longitude while the Sun turns; the
    # longitude from the central meridian adds to that of the disk centre
    return round(float((centre_longitude + longitude) % 360), 2)


class MagneticFieldPanel(PanelConfig):
    """The newest computed PFSS model of the coronal magnetic field, drawn as a turning Sun with field lines or as a map.

    PFSS (potential field source surface) extends the measured field of the solar surface out to a sphere, the source
    surface, beyond which the solar wind carries the field lines away.

    Attributes:
        dataset: The dataset of the model files, one document per model naming its file.
        regions: The dataset of the daily sunspot region reports with location and Carrington longitude, marked on the
            Sun; None for no marks.
        coronal_holes: The image dataset whose frames hold the coronal holes found in them; the holes of the newest frame
            are drawn; None for none.
        events: The dataset of solar events with type, headline and place, whose flares and CMEs are marked at their
            time; None for none.
        surface_images: The image dataset of whole-Sun images with known disk that can cover the side of the Sun seen
            from Earth instead of its field; None for the field only.
        history: The span of models played, ending with the newest model.
    """

    dataset: str = Field(min_length=1)
    regions: str | None = None
    coronal_holes: str | None = None
    events: str | None = None
    surface_images: str | None = None
    history: IsoDuration = timedelta(days=1)

    def shown_fields(self) -> dict[str, list[str]]:
        """Lists the model file, the region positions and the coronal holes as the shown fields."""
        shown = {self.dataset: [FRAME_FILE_FIELD]}
        if self.regions is not None:
            shown[self.regions] = [REGION_NUMBER_FIELD, REGION_LOCATION_FIELD, CARRINGTON_LONGITUDE_FIELD]
        if self.coronal_holes is not None:
            shown[self.coronal_holes] = [CORONAL_HOLES_FIELD]
        if self.surface_images is not None:
            shown.setdefault(self.surface_images, []).append(FRAME_FILE_FIELD)
        return shown


class ModelFile(BaseModel):
    """One stored model of the magnetic field.

    Attributes:
        time: The time of its synoptic map.
        url: The path of its file relative to the data directory of the site.
    """

    model_config = PAYLOAD

    time: datetime
    url: str


class EventMark(BaseModel):
    """A flare or CME marked on the Sun at its time.

    Attributes:
        kind: The event type code, FLR or CME.
        label: The headline, such as the flare class or the CME speed; empty if none.
        time: The time of the event.
        latitude: The heliographic latitude of its place in degrees.
        carrington_longitude: The Carrington longitude of its place in degrees.
    """

    model_config = PAYLOAD

    kind: str
    label: str
    time: datetime
    latitude: float
    carrington_longitude: float


class HoleMark(BaseModel):
    """A coronal hole drawn on the Sun.

    Attributes:
        outline: The border as latitude and Carrington longitude in degrees.
        latitude: The latitude of its centre in degrees.
        carrington_longitude: The Carrington longitude of its centre in degrees.
    """

    model_config = PAYLOAD

    outline: list[tuple[float, float]]
    latitude: float
    carrington_longitude: float


class RegionMark(BaseModel):
    """A sunspot region marked on the Sun.

    Attributes:
        region: The NOAA region number.
        latitude: The heliographic latitude in degrees.
        carrington_longitude: The Carrington longitude in degrees.
        area: The area in millionths of the solar hemisphere; None if unknown.
        magnetic_class: The Mount Wilson class, the magnetic classification of sunspot groups, spelled out.
    """

    model_config = PAYLOAD

    region: int
    latitude: float
    carrington_longitude: float
    area: float | None
    magnetic_class: str


class SurfaceImage(BaseModel):
    """A whole-Sun image with what is needed to lay it onto the side of the Sun seen from Earth.

    Attributes:
        time: The time of the image.
        url: The path of its file relative to the data directory of the site.
        disk: The centre and radius of the solar disk as shares of the image width, from the top left corner.
        b0: The heliographic latitude of the disk centre in degrees, the tilt of the solar axis towards Earth.
        centre_longitude: The Carrington longitude of the disk centre in degrees.
    """

    model_config = PAYLOAD

    time: datetime
    url: str
    disk: tuple[float, float, float]
    b0: float
    centre_longitude: float


class MagneticFieldPayload(PanelPayload):
    """The JSON document of a magnetic field view; the model itself lies in its own file.

    Attributes:
        title: The heading.
        dataset: The dataset of the model files, which names their directory.
        url: The path of the newest model file relative to the data directory of the site; None without model.
        regions: The sunspot regions of the newest report.
        coronal_holes: The coronal holes found in the newest image.
        holes_time: The time of that image; None without image.
        models: The stored models of the span, oldest first, for playing the field over time.
        events: The flares and CMEs of the span with known place.
        surface_dataset: The dataset of the surface images, which names their directory; None without them.
        surface_images: The whole-Sun images of the span, oldest first.
        map_time: The time of the synoptic map of the model; None without model.
        reference: The publications of the model and the software.
        freshness: How current the model is.
    """

    title: str
    dataset: str
    url: str | None
    regions: list[RegionMark] = Field(default_factory=list)
    coronal_holes: list[HoleMark] = Field(default_factory=list)
    holes_time: datetime | None = None
    models: list[ModelFile] = Field(default_factory=list)
    events: list[EventMark] = Field(default_factory=list)
    surface_dataset: str | None = None
    surface_images: list[SurfaceImage] = Field(default_factory=list)
    map_time: datetime | None
    reference: str
    freshness: list[DatasetFreshness]

    def media_files(self) -> list[tuple[str, str]]:
        """Lists the model and image files the payload refers to as pairs of dataset ID and file name."""
        return [media_file(model.url) for model in self.models] + [media_file(image.url) for image in self.surface_images]


def _region_marks(dataset: str | None, reader: DashboardReader) -> list[RegionMark]:
    """Reads the sunspot regions of the newest daily report with their latitude and Carrington longitude."""
    if dataset is None:
        return []
    reports = region_reports(reader, dataset, REGION_REPORT_SPAN)
    if reports.empty or CARRINGTON_LONGITUDE_FIELD not in reports.columns:
        return []
    # All regions of the newest report share its time stamp
    newest = reports[reports.index == pd.DatetimeIndex(reports.index).max()]
    marks = []
    for _, report in newest.iterrows():
        longitude = pd.to_numeric(report[CARRINGTON_LONGITUDE_FIELD], errors="coerce")
        if pd.isna(longitude):
            continue
        area = pd.to_numeric(report[REGION_AREA_FIELD], errors="coerce")
        marks.append(
            RegionMark(
                region=int(report[REGION_NUMBER_FIELD]),
                latitude=float(report[REGION_LATITUDE_COLUMN]),
                carrington_longitude=float(longitude),
                area=None if pd.isna(area) else float(area),
                magnetic_class=magnetic_class_name(report[MAGNETIC_CLASS_FIELD]),
            )
        )
    return marks


# Kinds of event marked on the Sun: flares and coronal mass ejections
MARKED_EVENTS = (DonkiEventType.FLARE, DonkiEventType.CME)


def _event_marks(dataset: str | None, history: timedelta, reader: DashboardReader) -> list[EventMark]:
    """Reads the flares and CMEs of a span with known place and turns their place into Carrington coordinates."""
    if dataset is None:
        return []
    events = reader.recent(dataset, history, (EVENT_TYPE_FIELD, LABEL_FIELD, LOCATION_FIELD))
    if events.empty or LOCATION_FIELD not in events.columns:
        return []
    marks = []
    marked = events[events[EVENT_TYPE_FIELD].isin(MARKED_EVENTS)]
    for moment, (_, event) in zip(pd.DatetimeIndex(marked.index), marked.iterrows(), strict=True):
        coordinates = location_coordinates(str(event[LOCATION_FIELD] or ""))
        if coordinates is None:
            continue
        marks.append(
            EventMark(
                kind=str(event[EVENT_TYPE_FIELD]),
                label=text_or_empty(event[LABEL_FIELD]),
                time=moment.to_pydatetime(),
                latitude=coordinates[0],
                # L0 is the Carrington longitude of the disk centre seen from Earth at the time of the event
                carrington_longitude=_carrington_longitude(float(sun.L0(moment).to_value(u.deg)), coordinates[1]),
            )
        )
    return marks


def _hole_marks(dataset: str | None, reader: DashboardReader) -> tuple[list[HoleMark], datetime | None]:
    """Reads the coronal holes of the newest frame and turns their positions into Carrington coordinates."""
    if dataset is None:
        return [], None
    latest = reader.latest(dataset, (CORONAL_HOLES_FIELD, *DISK_FIELDS))
    # Without a known disk the outline points cannot be turned into positions on the Sun
    if latest is None or not all(isinstance(latest[1].get(field), int | float) for field in DISK_FIELDS):
        return [], None
    time, values = latest
    disk_x, disk_y, radius = (float(values[field]) for field in DISK_FIELDS)
    times = pd.DatetimeIndex([time])
    tilt = float(solar_b0(times)[0])
    # The Carrington longitude of the disk centre; a longitude from the central meridian adds to it
    centre_longitude = float(sun.L0(time).to_value(u.deg))
    marks = []
    for hole in parse_coronal_holes(values.get(CORONAL_HOLES_FIELD)):
        points = np.asarray(hole.outline, dtype=float)
        # Image shares become solar radii from the disk centre; the image y axis points down, north points up
        latitude, longitude = heliographic_position((points[:, 0] - disk_x) / radius, (disk_y - points[:, 1]) / radius, np.full(len(points), tilt))
        marks.append(
            HoleMark(
                outline=[(round(float(lat), 2), _carrington_longitude(centre_longitude, lon)) for lat, lon in zip(latitude, longitude, strict=True)],
                latitude=hole.latitude,
                carrington_longitude=_carrington_longitude(centre_longitude, hole.longitude),
            )
        )
    return marks, time


def _surface_images(dataset: str | None, history: timedelta, reader: DashboardReader) -> list[SurfaceImage]:
    """Reads the whole-Sun images of the span with known disk, each with the view from Earth at its time."""
    if dataset is None:
        return []
    frames = reader.recent(dataset, history, (FRAME_FILE_FIELD, *DISK_FIELDS))
    frames = frames.reindex(columns=[FRAME_FILE_FIELD, *DISK_FIELDS]).dropna()
    if frames.empty:
        return []
    times = pd.DatetimeIndex(frames.index)
    tilts = solar_b0(times)
    # sunpy reads naive datetimes as UTC
    centre_longitudes = sun.L0(times.tz_convert("UTC").tz_localize(None).to_pydatetime()).to_value(u.deg)
    return [
        SurfaceImage(
            time=time.to_pydatetime(),
            url=frame_url(dataset, str(row[FRAME_FILE_FIELD])),
            disk=(float(row[DISK_FIELDS[0]]), float(row[DISK_FIELDS[1]]), float(row[DISK_FIELDS[2]])),
            b0=round(float(tilt), 4),
            centre_longitude=round(float(longitude), 4),
        )
        # atleast_1d, as sunpy returns a scalar for a single time
        for time, (_, row), tilt, longitude in zip(times, frames.iterrows(), tilts, np.atleast_1d(centre_longitudes), strict=True)
    ]


def build_magnetic_field(panel: MagneticFieldPanel, reader: DashboardReader, context: PanelContext) -> MagneticFieldPayload:
    """Builds the JSON document of a magnetic field view from the newest model document.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export.

    Returns:
        The payload.
    """
    latest = reader.latest(panel.dataset, (FRAME_FILE_FIELD,))
    file_name = latest[1].get(FRAME_FILE_FIELD) if latest is not None else None
    # Without a stored model the panel shows only its heading and the publications
    if latest is None or not isinstance(file_name, str):
        return MagneticFieldPayload(
            type=panel.type, title=panel.title, dataset=panel.dataset, url=None, map_time=None, reference=PFSS_REFERENCE, freshness=[]
        )
    freshness = [dataset_freshness(panel.dataset, latest[0], reader.metadata(panel.dataset), context.feed_intervals.get(panel.dataset))]
    holes, holes_time = _hole_marks(panel.coronal_holes, reader)
    stored = reader.recent(panel.dataset, panel.history, (FRAME_FILE_FIELD,))
    models = (
        [
            ModelFile(time=time.to_pydatetime(), url=frame_url(panel.dataset, str(name)))
            for time, name in zip(pd.DatetimeIndex(stored[FRAME_FILE_FIELD].dropna().index), stored[FRAME_FILE_FIELD].dropna(), strict=True)
        ]
        if FRAME_FILE_FIELD in stored.columns
        else []
    )
    return MagneticFieldPayload(
        models=models,
        events=_event_marks(panel.events, panel.history, reader),
        surface_dataset=panel.surface_images,
        surface_images=_surface_images(panel.surface_images, panel.history, reader),
        regions=_region_marks(panel.regions, reader),
        coronal_holes=holes,
        holes_time=holes_time,
        type=panel.type,
        title=panel.title,
        dataset=panel.dataset,
        url=frame_url(panel.dataset, file_name),
        map_time=latest[0],
        reference=PFSS_REFERENCE,
        freshness=freshness,
    )
