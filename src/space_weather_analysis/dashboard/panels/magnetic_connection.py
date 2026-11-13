from datetime import datetime

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from space_weather_analysis.clients.donki import (
    EVENT_ID_FIELD,
    EVENT_TYPE_FIELD,
    LABEL_FIELD,
    LINK_FIELD,
    LINK_SEPARATOR,
    LINKED_FIELD,
    LOCATION_FIELD,
    DonkiEventType,
)
from space_weather_analysis.clients.ephemeris import BODY_FIELD, DISTANCE_FIELD, LONGITUDE_FIELD
from space_weather_analysis.dashboard.freshness import DatasetFreshness
from space_weather_analysis.dashboard.labels import field_label, field_unit
from space_weather_analysis.dashboard.panels.base import CLOSED, PAYLOAD, PanelConfig, PanelContext, PanelPayload, text_or_empty
from space_weather_analysis.dashboard.panels.heliosphere import POSITION_MARGIN, read_hourly_wind
from space_weather_analysis.dashboard.panels.series import SourceRef, index_times, read_freshness
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.heliosphere.propagation import AU_KM, SOLAR_RADIUS_KM, spiral_longitudes
from space_weather_analysis.solar.geometry import location_coordinates
from space_weather_analysis.util.time_utils import IsoDuration

# Spread of the intensity of particle events around the foot of the field line through the observer: a Gaussian of this
# width in degrees fits the events seen by several spacecraft (Richardson et al. 2014, Solar Physics 289, 3059,
# doi:10.1007/s11207-014-0524-8)
MAGNETIC_CONNECTION_WIDTH = 43.0
# Step of the particle flux sent
FLUX_STEP = "10min"
# Radius of the Sun in astronomical units, where the field lines end
SOLAR_RADIUS_AU = SOLAR_RADIUS_KM / AU_KM


class ParticleChannel(BaseModel):
    """One measured series of energetic particles of an observer.

    Attributes:
        dataset: The dataset ID.
        field: The data field.
        label: The name in legend and hover; empty takes the label from the metadata.
    """

    model_config = CLOSED

    dataset: str = Field(min_length=1)
    field: str = Field(min_length=1)
    label: str = ""


class MagneticConnectionObserver(BaseModel):
    """A place in the solar wind whose field line to the Sun is drawn, with the particles measured there.

    Attributes:
        body: The name of the body in the positions dataset, such as Earth or STEREO A.
        wind: The solar wind speed measured there, which sets how far its field line winds.
        particles: The particle series measured there.
    """

    model_config = CLOSED

    body: str = Field(min_length=1)
    wind: SourceRef
    particles: list[ParticleChannel] = Field(default_factory=list)


class MagneticConnectionPanel(PanelConfig):
    """Where the field lines through Earth and other observers meet the Sun, with the flares and particles of each.

    The field lines follow the Parker spiral, the shape the rotating Sun winds its field into while the solar wind carries
    it outwards; energetic particles from a flare reach an observer most easily along the line through it.

    Attributes:
        events: The dataset of events of DONKI (Database Of Notifications, Knowledge, Information of the NASA CCMC)
            with the place of each flare and the particle events linked to them.
        positions: The dataset of the daily positions of planets and spacecraft.
        observers: The observers, Earth first.
        history: The span shown, ending now.
    """

    events: str = Field(min_length=1)
    positions: str = Field(min_length=1)
    observers: list[MagneticConnectionObserver] = Field(min_length=1)
    history: IsoDuration


class FlareMark(BaseModel):
    """One flare with its place and its angle to the foot of the field line through each observer.

    Attributes:
        time: The UTC peak time.
        label: The X-ray class.
        latitude: The heliographic latitude in degrees.
        longitude: The longitude from the central meridian seen from Earth in degrees, west positive.
        connection_angles: The longitude of the flare minus that of the footpoint of each observer at its time in degrees,
            by observer; None for an observer without wind or position then.
        particles: Whether DONKI links a particle event to it.
        link: The DONKI page.
    """

    model_config = PAYLOAD

    time: datetime
    label: str
    latitude: float
    longitude: float
    connection_angles: dict[str, float | None]
    particles: bool
    link: str


class FluxSeries(BaseModel):
    """One particle series of an observer, the highest value of each step.

    Attributes:
        label: The name in legend and hover.
        unit: The unit; empty if the provider names none.
        times: The UTC times.
        values: The values.
    """

    model_config = PAYLOAD

    label: str
    unit: str
    times: list[datetime]
    values: list[float]


class ObserverPayload(BaseModel):
    """The field line and particles of one observer.

    Attributes:
        name: The name of the body.
        times: The UTC times of the footpoints and positions, hourly where wind was measured.
        footpoints: The longitude of the foot of its field line from the central meridian seen from Earth in degrees,
            west positive.
        wind_speeds: The measured wind speed each footpoint was computed with in km/s.
        longitudes: The heliographic longitude of the observer from the Sun-Earth line in degrees, west positive.
        distances: The distance of the observer from the Sun in astronomical units.
        fluxes: The particle series measured there.
    """

    model_config = PAYLOAD

    name: str
    times: list[datetime]
    footpoints: list[float]
    wind_speeds: list[float]
    longitudes: list[float]
    distances: list[float]
    fluxes: list[FluxSeries]


class ParticleEvent(BaseModel):
    """One energetic particle event DONKI lists.

    Attributes:
        time: The UTC time DONKI lists it at.
        link: The DONKI page.
    """

    model_config = PAYLOAD

    time: datetime
    link: str


class MagneticConnectionPayload(PanelPayload):
    """The JSON document of the view of the magnetic connection between the Sun and the observers.

    Attributes:
        title: The heading.
        start: The start of the shown span.
        width: The spread of particle events around the footpoint in degrees.
        observers: The observers, Earth first.
        flares: The flares with known place.
        particle_events: The particle events DONKI lists.
        freshness: How current each dataset is.
    """

    title: str
    start: datetime
    width: float
    observers: list[ObserverPayload]
    flares: list[FlareMark]
    particle_events: list[ParticleEvent]
    freshness: list[DatasetFreshness]


def footpoint_longitudes(longitudes: np.ndarray, distances: np.ndarray, wind_speeds: np.ndarray) -> np.ndarray:
    """Computes where the Parker spiral field lines through places meet the Sun, as longitudes from the central meridian, west positive.

    Args:
        longitudes: The heliographic longitudes of the places in degrees, west positive.
        distances: Their distances from the Sun in astronomical units.
        wind_speeds: The solar wind speed at each place in km/s.

    Returns:
        The footpoint longitudes in degrees, one per place.
    """
    return spiral_longitudes(np.full_like(distances, SOLAR_RADIUS_AU, dtype=float), longitudes, distances, wind_speeds)


def _wrapped_longitude[Degrees: (float, np.ndarray)](degrees: Degrees) -> Degrees:
    """Wraps longitudes or longitude differences in degrees to the range from -180 to below +180."""
    # Shifting by 180 before the modulo and back after it centres the range of the modulo on 0
    return (degrees + 180) % 360 - 180


def _track(positions: pd.DataFrame, body: str, times: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray] | None:
    """Interpolates the daily longitude and distance of a body to the given times; None without positions."""
    if positions.empty or BODY_FIELD not in positions.columns:
        return None
    rows = positions[positions[BODY_FIELD] == body].dropna(subset=[LONGITUDE_FIELD, DISTANCE_FIELD])
    if rows.empty:
        return None
    # Times as integer nanoseconds, the numbers np.interp interpolates over
    known = pd.DatetimeIndex(rows.index).as_unit("ns").to_numpy(dtype=np.int64)
    wanted = times.as_unit("ns").to_numpy(dtype=np.int64)
    # Longitudes jump from +180 to -180 degrees, so they are interpolated unwrapped and wrapped again
    unwrapped = np.degrees(np.unwrap(np.radians(rows[LONGITUDE_FIELD].to_numpy(dtype=float))))
    longitudes = _wrapped_longitude(np.interp(wanted, known, unwrapped))
    return longitudes, np.interp(wanted, known, rows[DISTANCE_FIELD].to_numpy(dtype=float))


def _flux(reader: DashboardReader, channel: ParticleChannel, history: pd.Timedelta) -> tuple[FluxSeries, pd.DataFrame]:
    """Reads one particle series as the highest positive value of each step, with the read documents for the freshness."""
    frame = reader.recent(channel.dataset, history.to_pytimedelta(), (channel.field,))
    values = pd.to_numeric(frame.get(channel.field, pd.Series(dtype=float)), errors="coerce").dropna()
    # Fluxes are drawn on a log axis, so only positive values count; the maximum keeps the peak of each step
    values = values[values > 0]
    values = values.resample(FLUX_STEP).max().dropna() if not values.empty else values
    record = reader.metadata(channel.dataset)
    series = FluxSeries(
        label=channel.label or field_label(record, channel.field),
        unit=field_unit(record, channel.field),
        times=index_times(values),
        values=values.tolist(),
    )
    return series, frame


def _flares(events: pd.DataFrame, footpoints: dict[str, pd.Series]) -> list[FlareMark]:
    """Reads the flares (DONKI type FLR) with known place and their angle to the footpoint of each observer at their time.

    A flare counts as linked to particles if DONKI links it to a solar energetic particle event (DONKI type SEP).
    """
    flares = events[events[EVENT_TYPE_FIELD] == DonkiEventType.FLARE]
    particle_ids = set(events.loc[events[EVENT_TYPE_FIELD] == DonkiEventType.SOLAR_ENERGETIC_PARTICLES, EVENT_ID_FIELD].astype(str))
    marks = []
    for moment, (_, flare) in zip(pd.DatetimeIndex(flares.index), flares.iterrows(), strict=True):
        coordinates = location_coordinates(str(flare[LOCATION_FIELD] or ""))
        if coordinates is None:
            continue
        angles: dict[str, float | None] = {}
        # The angle to each observer takes its footpoint of the hour nearest to the flare; -1 marks an observer without footpoints
        for name, series in footpoints.items():
            nearest = series.index.get_indexer([moment], method="nearest")[0] if not series.empty else -1
            # The angle is wrapped to +-180 degrees, so a flare just across the far side counts as close
            angles[name] = round(_wrapped_longitude(coordinates[1] - float(series.iloc[nearest])), 1) if nearest >= 0 else None
        # DONKI stores the IDs of the linked events in one text, joined by LINK_SEPARATOR
        linked = text_or_empty(flare[LINKED_FIELD])
        marks.append(
            FlareMark(
                time=moment.to_pydatetime(),
                label=text_or_empty(flare[LABEL_FIELD]),
                latitude=coordinates[0],
                longitude=coordinates[1],
                connection_angles=angles,
                particles=any(linked_id in particle_ids for linked_id in linked.split(LINK_SEPARATOR)),
                link=text_or_empty(flare[LINK_FIELD]),
            )
        )
    return marks


def build_magnetic_connection(panel: MagneticConnectionPanel, reader: DashboardReader, context: PanelContext) -> MagneticConnectionPayload:
    """Builds the JSON document of the view of the magnetic connection between the Sun and the observers.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export, with the time of the drawing and the feed interval of each dataset.

    Returns:
        The payload.
    """
    now = pd.Timestamp(context.now)
    history = pd.Timedelta(panel.history)
    positions = reader.recent(panel.positions, (history + POSITION_MARGIN).to_pytimedelta(), (BODY_FIELD, LONGITUDE_FIELD, DISTANCE_FIELD))
    events = reader.recent(panel.events, panel.history, (EVENT_TYPE_FIELD, EVENT_ID_FIELD, LINKED_FIELD, LABEL_FIELD, LINK_FIELD, LOCATION_FIELD))
    observers = []
    footpoints: dict[str, pd.Series] = {}
    read: list[tuple[str, pd.DataFrame | pd.Series]] = [(panel.events, events)]
    for observer in panel.observers:
        wind = read_hourly_wind(reader, observer.wind, history.to_pytimedelta())
        read.append((observer.wind.dataset, wind))
        # Footpoints exist only for the hours with both a measured wind and a known position of the observer
        track = _track(positions, observer.body, pd.DatetimeIndex(wind.index)) if not wind.empty else None
        speeds = wind.to_numpy(dtype=float)
        longitudes, distances = track if track is not None else (np.array([]), np.array([]))
        feet = footpoint_longitudes(longitudes, distances, speeds) if track is not None else np.array([])
        footpoints[observer.body] = pd.Series(feet, index=wind.index if track is not None else pd.DatetimeIndex([], tz="UTC"))
        fluxes = []
        for channel in observer.particles:
            series, frame = _flux(reader, channel, history)
            fluxes.append(series)
            read.append((channel.dataset, frame))
        observers.append(
            ObserverPayload(
                name=observer.body,
                times=index_times(footpoints[observer.body]),
                footpoints=np.round(feet, 1).tolist(),
                wind_speeds=np.round(speeds, 1).tolist() if track is not None else [],
                longitudes=np.round(longitudes, 2).tolist(),
                distances=np.round(distances, 4).tolist(),
                fluxes=fluxes,
            )
        )
    # Particle events are listed on their own besides the flares, also those DONKI links to no flare
    particle_rows = events[events[EVENT_TYPE_FIELD] == DonkiEventType.SOLAR_ENERGETIC_PARTICLES] if not events.empty else events
    # A dataset read several times is described once, at the place of its first read with the values of its last read
    freshness = {entry.dataset: entry for entry in read_freshness(read, reader.metadata, context)}
    return MagneticConnectionPayload(
        type=panel.type,
        title=panel.title,
        start=(now - history).to_pydatetime(),
        width=MAGNETIC_CONNECTION_WIDTH,
        observers=observers,
        flares=_flares(events, footpoints) if not events.empty else [],
        particle_events=[
            ParticleEvent(time=moment.to_pydatetime(), link=text_or_empty(link))
            for moment, link in zip(pd.DatetimeIndex(particle_rows.index), particle_rows[LINK_FIELD] if not particle_rows.empty else [], strict=True)
        ],
        freshness=list(freshness.values()),
    )
