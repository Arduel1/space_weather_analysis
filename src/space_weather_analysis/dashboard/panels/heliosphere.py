import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from space_weather_analysis.clients.donki import (
    CME_HALF_ANGLE_FIELD,
    CME_LATITUDE_FIELD,
    CME_LONGITUDE_FIELD,
    CME_TIME_21_5_FIELD,
    EARTH,
    EVENT_ID_FIELD,
    EVENT_TYPE_FIELD,
    IMPACTS_FIELD,
    LABEL_FIELD,
    LINK_FIELD,
    LINK_SEPARATOR,
    LINKED_FIELD,
    VALUE_FIELD,
    DonkiEventType,
)
from space_weather_analysis.clients.ephemeris import BODY_FIELD, DISTANCE_FIELD, KIND_FIELD, LATITUDE_FIELD, LONGITUDE_FIELD
from space_weather_analysis.dashboard.freshness import DatasetFreshness
from space_weather_analysis.dashboard.panels.base import PAYLOAD, PanelConfig, PanelContext, PanelPayload, finite_number, text_or_empty
from space_weather_analysis.dashboard.panels.series import SourceRef, index_times, read_freshness
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.heliosphere.propagation import drag_based_distance, ecliptic_half_width
from space_weather_analysis.util.time_utils import IsoDuration

# Time between two computed distances of a CME front
FLIGHT_STEP = timedelta(hours=2)
# How long the flight of a CME is followed after its launch
FLIGHT_DURATION = timedelta(days=12)
# Distance from the Sun in astronomical units after which a flight is no longer followed, beyond the orbit of Jupiter
FLIGHT_REACH_AU = 6.0
# Solar wind speed in km/s taken for the drag of a CME without measured wind before its start: the typical speed of the
# slow solar wind (Schwenn, R. (2006), Space Weather: The Solar Perspective, Living Rev. Sol. Phys. 3, 2,
# doi:10.12942/lrsp-2006-2)
DEFAULT_WIND_SPEED = 400.0
# Span before the start of a CME over which the measured wind speed sets the drag of its flight
WIND_BEFORE_START = timedelta(days=1)
# Step of the wind speeds sent for the field lines and of the footpoints computed from them
WIND_STEP = "1h"
# Span added to the daily positions read, so that the first and last hours of a span find the positions of the days
# around them
POSITION_MARGIN = timedelta(days=2)
# Fields of the position documents the view reads
POSITION_READ_FIELDS = (BODY_FIELD, KIND_FIELD, LONGITUDE_FIELD, LATITUDE_FIELD, DISTANCE_FIELD)
# Fields of the event documents of DONKI (Database Of Notifications, Knowledge, Information of the NASA CCMC) the view reads
EVENT_READ_FIELDS = (
    EVENT_TYPE_FIELD,
    EVENT_ID_FIELD,
    LINKED_FIELD,
    LABEL_FIELD,
    VALUE_FIELD,
    LINK_FIELD,
    CME_LATITUDE_FIELD,
    CME_LONGITUDE_FIELD,
    CME_HALF_ANGLE_FIELD,
    CME_TIME_21_5_FIELD,
    IMPACTS_FIELD,
)


class HeliospherePanel(PanelConfig):
    """The inner solar system seen from above: planets, spacecraft, the field lines of the solar wind and the CMEs flying out.

    CME stands for coronal mass ejection, a cloud of plasma thrown out of the Sun.

    Attributes:
        positions: The dataset of the daily positions of planets and spacecraft.
        events: The dataset of events of DONKI (Database Of Notifications, Knowledge, Information of the NASA CCMC)
            with the fitted flight and expected arrivals of each CME and the observed interplanetary shocks.
        winds: The measured solar wind speed by the body it is measured at, which bends the field line through it.
        history: The span of events shown, ending now.
        ahead: The span after now that the replay reaches, for arrivals expected in the coming days.
    """

    positions: str = Field(min_length=1)
    events: str = Field(min_length=1)
    winds: dict[str, SourceRef] = Field(default_factory=dict)
    history: IsoDuration
    ahead: IsoDuration = timedelta(days=5)

    def shown_fields(self) -> dict[str, list[str]]:
        """Lists the positions, the CME flights and the wind speeds as the shown fields."""
        shown = {
            self.positions: [LONGITUDE_FIELD, LATITUDE_FIELD, DISTANCE_FIELD],
            self.events: [VALUE_FIELD, CME_LATITUDE_FIELD, CME_LONGITUDE_FIELD, CME_HALF_ANGLE_FIELD, IMPACTS_FIELD],
        }
        for ref in self.winds.values():
            shown.setdefault(ref.dataset, []).append(ref.field)
        return shown


class BodyTrack(BaseModel):
    """The daily positions of one planet or spacecraft.

    Attributes:
        name: The name as DONKI gives it.
        kind: planet or spacecraft.
        times: The UTC times of the positions.
        longitude: The heliographic longitudes from the Sun-Earth line in degrees, west positive.
        latitude: The heliographic latitudes in degrees.
        distance: The distances from the Sun in astronomical units.
    """

    model_config = PAYLOAD

    name: str
    kind: str
    times: list[datetime]
    longitude: list[float]
    latitude: list[float]
    distance: list[float]


class WindSeries(BaseModel):
    """The measured solar wind speed at one body, hourly.

    Attributes:
        body: The name of the body.
        times: The UTC start of each hour.
        speeds: The median speed of each hour in km/s.
    """

    model_config = PAYLOAD

    body: str
    times: list[datetime]
    speeds: list[float]


class Arrival(BaseModel):
    """The arrival of a CME or shock at a body, expected by a model or observed.

    Attributes:
        location: The body as DONKI names it.
        time: The UTC time of the arrival.
        glancing: Whether the CME only grazes the body.
        link: The DONKI page of the observed shock; empty for expected arrivals.
    """

    model_config = PAYLOAD

    location: str
    time: datetime
    glancing: bool = False
    link: str = ""


class CmeFlight(BaseModel):
    """One CME with its fitted direction and the distances of its front over time after the drag-based model.

    The drag-based model lets the solar wind slow down a fast CME or speed up a slow one towards the wind speed.

    Attributes:
        id: The DONKI activity ID.
        label: The headline, its speed.
        link: The DONKI page of the CME.
        start: The UTC time DONKI lists the CME at, its first sight in a coronagraph.
        launch: The UTC time its front passed 21.5 solar radii, the first of the distances.
        latitude: The latitude of its direction in degrees.
        longitude: The longitude of its direction from the Sun-Earth line in degrees, west positive.
        half_angle: Half its opening angle in degrees.
        ecliptic_half_width: Half the width of its cut with the plane of the planets in degrees; None if it passes above
            or below.
        speed: Its speed at 21.5 solar radii in km/s.
        wind_speed: The solar wind speed its flight was computed with in km/s.
        step_hours: The time between two distances in hours.
        distances: The distances of its front from the Sun in astronomical units, the first at the launch.
        expected: Where and when WSA-Enlil, the solar wind model run by NASA CCMC for every CME, expects it to arrive.
        observed: The interplanetary shocks DONKI links to it.
    """

    model_config = PAYLOAD

    id: str
    label: str
    link: str
    start: datetime
    launch: datetime
    latitude: float
    longitude: float
    half_angle: float
    ecliptic_half_width: float | None
    speed: float
    wind_speed: float
    step_hours: float
    distances: list[float]
    expected: list[Arrival]
    observed: list[Arrival]


class HeliospherePayload(PanelPayload):
    """The JSON document of the view of the inner solar system.

    Attributes:
        title: The heading.
        start: The start of the replay span.
        stop: The end of the replay span, after now.
        bodies: The tracks of the planets and spacecraft.
        winds: The measured wind speeds bending the field lines.
        cmes: The CMEs with known direction, oldest first.
        shocks: The observed interplanetary shocks, also those of no CME.
        streams: The high speed streams from coronal holes DONKI lists at Earth.
        freshness: How current each dataset is.
    """

    title: str
    start: datetime
    stop: datetime
    bodies: list[BodyTrack]
    winds: list[WindSeries]
    cmes: list[CmeFlight]
    shocks: list[Arrival]
    streams: list[Arrival]
    freshness: list[DatasetFreshness]


def _tracks(positions: pd.DataFrame) -> list[BodyTrack]:
    """Groups the daily positions by body, Earth first."""
    if positions.empty or BODY_FIELD not in positions.columns:
        return []
    tracks = []
    for name, rows in positions.groupby(BODY_FIELD, sort=False):
        # A position counts only with all three coordinates
        valid = rows.dropna(subset=[LONGITUDE_FIELD, LATITUDE_FIELD, DISTANCE_FIELD])
        if valid.empty:
            continue
        tracks.append(
            BodyTrack(
                name=str(name),
                kind=text_or_empty(valid[KIND_FIELD].iloc[0]),
                times=index_times(valid),
                longitude=valid[LONGITUDE_FIELD].astype(float).round(3).tolist(),
                latitude=valid[LATITUDE_FIELD].astype(float).round(3).tolist(),
                distance=valid[DISTANCE_FIELD].astype(float).round(4).tolist(),
            )
        )
    # False sorts before True, and the sort is stable, so Earth comes first and the others keep their order
    return sorted(tracks, key=lambda track: track.name != EARTH)


def read_hourly_wind(reader: DashboardReader, ref: SourceRef, history: timedelta) -> pd.Series:
    """Reads a measured solar wind speed over a history as hourly medians without empty hours.

    Args:
        reader: The source of documents.
        ref: The stored speed field in km/s.
        history: The span read, ending with the newest stored document.

    Returns:
        The median of each hour with measurements; empty without positive speeds.
    """
    frame = reader.recent(ref.dataset, history, (ref.field,))
    # Text and non-positive values are no speed measurement
    speeds = pd.to_numeric(frame.get(ref.field, pd.Series(dtype=float)), errors="coerce").dropna()
    speeds = speeds[speeds > 0]
    if speeds.empty:
        return speeds
    return speeds.resample(WIND_STEP).median().dropna()


def _wind_before(wind: pd.Series, launch: pd.Timestamp) -> float:
    """Returns the median measured wind speed of the day before a launch, else of all measurements, else DEFAULT_WIND_SPEED."""
    if wind.empty:
        return DEFAULT_WIND_SPEED
    # The wind measured in the day before the launch stands for the wind the CME runs into
    before = wind[(wind.index >= launch - WIND_BEFORE_START) & (wind.index < launch)]
    return float(before.median()) if not before.empty else float(wind.median())


def _impacts(text: object) -> list[Arrival]:
    """Reads the expected arrivals of a CME from their JSON text; none for missing or unreadable text."""
    try:
        entries = json.loads(text) if isinstance(text, str) else []
    except json.JSONDecodeError:
        return []
    arrivals = []
    # Each entry names a body and the expected arrival time; entries without body or readable time are skipped
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not entry.get("location"):
            continue
        time = pd.to_datetime(str(entry.get("arrival") or ""), utc=True, errors="coerce")
        if not pd.isna(time):
            arrivals.append(Arrival(location=str(entry["location"]), time=time.to_pydatetime(), glancing=bool(entry.get("glancing"))))
    return arrivals


def _streams(events: pd.DataFrame) -> list[Arrival]:
    """Reads the high speed streams (DONKI type HSS) from coronal holes, each at the time it reached Earth."""
    streams = events[events[EVENT_TYPE_FIELD] == DonkiEventType.HIGH_SPEED_STREAM]
    return [
        Arrival(location=EARTH, time=moment.to_pydatetime(), link=text_or_empty(link))
        for moment, link in zip(pd.DatetimeIndex(streams.index), streams[LINK_FIELD], strict=True)
    ]


def _shocks(events: pd.DataFrame) -> tuple[list[Arrival], dict[str, list[Arrival]]]:
    """Reads the observed interplanetary shocks (DONKI type IPS), and those linked to each CME by the CME ID."""
    shocks = events[events[EVENT_TYPE_FIELD] == DonkiEventType.INTERPLANETARY_SHOCK]
    observed: list[Arrival] = []
    by_cme: dict[str, list[Arrival]] = {}
    for moment, (_, shock) in zip(pd.DatetimeIndex(shocks.index), shocks.iterrows(), strict=True):
        location = text_or_empty(shock[LABEL_FIELD])
        if not location:
            continue
        arrival = Arrival(location=location, time=moment.to_pydatetime(), link=text_or_empty(shock[LINK_FIELD]))
        observed.append(arrival)
        for linked in text_or_empty(shock[LINKED_FIELD]).split(LINK_SEPARATOR):
            # DONKI activity IDs name their event type, so only the linked CMEs contain it
            if DonkiEventType.CME in linked:
                by_cme.setdefault(linked, []).append(arrival)
    return observed, by_cme


def _flights(events: pd.DataFrame, wind: pd.Series, observed: dict[str, list[Arrival]]) -> list[CmeFlight]:
    """Computes the flight of every CME with fitted direction, speed and launch time."""
    cmes = events[events[EVENT_TYPE_FIELD] == DonkiEventType.CME]
    flights = []
    step_seconds = FLIGHT_STEP.total_seconds()
    # Seconds after the launch of every computed distance, the last at FLIGHT_DURATION
    seconds = np.arange(0, FLIGHT_DURATION.total_seconds() + step_seconds, step_seconds)
    for moment, (_, cme) in zip(pd.DatetimeIndex(cmes.index), cmes.iterrows(), strict=True):
        latitude, longitude = finite_number(cme.get(CME_LATITUDE_FIELD)), finite_number(cme.get(CME_LONGITUDE_FIELD))
        half_angle, speed = finite_number(cme.get(CME_HALF_ANGLE_FIELD)), finite_number(cme.get(VALUE_FIELD))
        launch = pd.to_datetime(text_or_empty(cme.get(CME_TIME_21_5_FIELD)), utc=True, errors="coerce")
        # Without direction, width, speed or launch time no flight can be drawn
        if latitude is None or longitude is None or half_angle is None or speed is None or pd.isna(launch):
            continue
        wind_speed = _wind_before(wind, launch)
        distances = drag_based_distance(seconds, speed, wind_speed)
        # The distances grow with time, so the flight ends with the first distance at or beyond FLIGHT_REACH_AU
        distances = distances[: int(np.searchsorted(distances, FLIGHT_REACH_AU)) + 1]
        event_id = text_or_empty(cme[EVENT_ID_FIELD])
        flights.append(
            CmeFlight(
                id=event_id,
                label=text_or_empty(cme[LABEL_FIELD]),
                link=text_or_empty(cme[LINK_FIELD]),
                start=moment.to_pydatetime(),
                launch=launch.to_pydatetime(),
                latitude=latitude,
                longitude=longitude,
                half_angle=half_angle,
                ecliptic_half_width=ecliptic_half_width(latitude, half_angle),
                speed=speed,
                wind_speed=round(wind_speed, 1),
                step_hours=FLIGHT_STEP / timedelta(hours=1),
                distances=np.round(distances, 4).tolist(),
                expected=_impacts(cme.get(IMPACTS_FIELD)),
                observed=observed.get(event_id, []),
            )
        )
    return flights


def build_heliosphere(panel: HeliospherePanel, reader: DashboardReader, context: PanelContext) -> HeliospherePayload:
    """Builds the JSON document of the view of the inner solar system.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export, with the time of the drawing and the feed interval of each dataset.

    Returns:
        The payload.
    """
    now = pd.Timestamp(context.now)
    start = now - pd.Timedelta(panel.history)
    # Positions reach into the coming days, as the replay continues to expected arrivals
    positions = reader.recent(panel.positions, panel.history + panel.ahead + POSITION_MARGIN, POSITION_READ_FIELDS)
    events = reader.recent(panel.events, panel.history, EVENT_READ_FIELDS)
    winds = {body: read_hourly_wind(reader, ref, panel.history) for body, ref in panel.winds.items()}
    # The wind at Earth sets the drag of every CME flight
    earth_wind = winds.get(EARTH, pd.Series(dtype=float))
    shocks, observed = _shocks(events) if not events.empty else ([], {})
    flights = _flights(events, earth_wind, observed) if not events.empty else []
    read: list[tuple[str, pd.DataFrame | pd.Series]] = [(panel.positions, positions), (panel.events, events)]
    read += [(ref.dataset, winds[body]) for body, ref in panel.winds.items()]
    return HeliospherePayload(
        type=panel.type,
        title=panel.title,
        start=start.to_pydatetime(),
        stop=(now + pd.Timedelta(panel.ahead)).to_pydatetime(),
        bodies=_tracks(positions),
        winds=[
            WindSeries(body=body, times=index_times(series), speeds=series.round(1).tolist()) for body, series in winds.items() if not series.empty
        ],
        cmes=flights,
        shocks=shocks,
        streams=_streams(events) if not events.empty else [],
        freshness=read_freshness(read, reader.metadata, context),
    )
