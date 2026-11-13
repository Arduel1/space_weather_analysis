from datetime import datetime, timedelta

import pandas as pd
from pydantic import BaseModel, Field

from space_weather_analysis.clients.image_sequences import FRAME_FILE_FIELD
from space_weather_analysis.dashboard.derived import DERIVED
from space_weather_analysis.dashboard.freshness import DatasetFreshness, dataset_freshness
from space_weather_analysis.dashboard.panels.base import PAYLOAD, PanelConfig, PanelContext, PanelPayload, finite_number
from space_weather_analysis.dashboard.panels.images import frame_url, media_file
from space_weather_analysis.dashboard.panels.series import SourceRef, index_times
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.geospace.aurora import DIPOLE_POLE_LATITUDE, DIPOLE_POLE_LONGITUDE, NIGHT_END_HOUR, NIGHT_START_HOUR
from space_weather_analysis.util.time_utils import IsoDuration

# Fields of a stored map document of OVATION Prime, the NOAA SWPC model of the aurora from the solar wind, as the OVATION
# pipeline writes them
EDGE_NORTH_FIELD = "edge_north"
EDGE_SOUTH_FIELD = "edge_south"
SUBSOLAR_LATITUDE_FIELD = "subsolar_latitude"
SUBSOLAR_LONGITUDE_FIELD = "subsolar_longitude"
# Fields of a map document the view reads
MAP_FIELDS = (FRAME_FILE_FIELD, EDGE_NORTH_FIELD, EDGE_SOUTH_FIELD, SUBSOLAR_LATITUDE_FIELD, SUBSOLAR_LONGITUDE_FIELD)
# The derived quantity estimating the edge of the oval from Kp, the planetary index of geomagnetic activity from 0 to 9
KP_EDGE = DERIVED["aurora_oval_edge"]
# Step the Kp values are combined in
KP_STEP = "1h"
# Extra span read from each Kp source, one three-hour Kp interval, so that the read covers the history even when the
# newest value lies after now
KP_READ_MARGIN = timedelta(hours=3)


class AuroraPanel(PanelConfig):
    """The aurora on a turnable Earth from the maps of the OVATION model, with the edge of the oval over time.

    Attributes:
        dataset: The dataset of the stored maps, one document per map naming its file.
        kp: The Kp values from which the rule of thumb for the edge of the oval is drawn.
        history: The span of maps played, ending with the newest map.
    """

    dataset: str = Field(min_length=1)
    kp: list[SourceRef] = Field(min_length=1)
    history: IsoDuration = timedelta(days=1)

    def shown_fields(self) -> dict[str, list[str]]:
        """Lists the map files with their edges and the Kp fields as the shown fields."""
        shown = {self.dataset: list(MAP_FIELDS)}
        for ref in self.kp:
            shown.setdefault(ref.dataset, []).append(ref.field)
        return shown


class AuroraFrame(BaseModel):
    """One stored map of the OVATION model.

    Attributes:
        time: The UTC time the map is valid for.
        url: The path of the map file relative to the data directory of the site.
        edge_north: The edge of the oval on the night side of the northern hemisphere in geomagnetic latitude; None
            without aurora there.
        edge_south: The same for the southern hemisphere, as absolute latitude.
        subsolar_latitude: The latitude where the Sun stands overhead in degrees.
        subsolar_longitude: The longitude where the Sun stands overhead in degrees, east positive.
    """

    model_config = PAYLOAD

    time: datetime
    url: str
    edge_north: float | None
    edge_south: float | None
    subsolar_latitude: float
    subsolar_longitude: float


class AuroraPayload(PanelPayload):
    """The JSON document of the aurora view.

    Attributes:
        title: The heading.
        start: The start of the span shown, the history before the time of the drawing.
        frames: The maps in time order.
        kp_times: The UTC times of the Kp values, newest source last where sources overlap.
        kp_values: The Kp values.
        kp_edges: The edge of the oval the rule of thumb gives for each Kp value, in geomagnetic latitude.
        kp_rule: The rule of thumb as formula with its source.
        pole_latitude: The latitude of the north pole of the geomagnetic dipole in degrees.
        pole_longitude: Its longitude in degrees, east positive.
        night_hours: The local hours counted as night side for the edges, start and end.
        freshness: How current each dataset is.
    """

    title: str
    start: datetime
    frames: list[AuroraFrame]
    kp_times: list[datetime]
    kp_values: list[float]
    kp_edges: list[float]
    kp_rule: str
    pole_latitude: float
    pole_longitude: float
    night_hours: tuple[float, float]
    freshness: list[DatasetFreshness]

    def media_files(self) -> list[tuple[str, str]]:
        """Lists the map files the payload refers to as pairs of dataset ID and file name."""
        return [media_file(frame.url) for frame in self.frames]


def _kp(panel: AuroraPanel, reader: DashboardReader, since: pd.Timestamp) -> pd.Series:
    """Joins the Kp sources from `since` on, each later source filling the times after the newest value of the ones before."""
    joined = pd.Series(dtype=float)
    for ref in panel.kp:
        frame = reader.recent(ref.dataset, panel.history + KP_READ_MARGIN, (ref.field,))
        values = pd.to_numeric(frame.get(ref.field, pd.Series(dtype=float)), errors="coerce").dropna()
        values = values[values.index >= since]
        # A later source only adds the times after the newest value joined so far
        if not joined.empty:
            values = values[values.index > joined.index.max()]
        joined = pd.concat([joined, values]) if not joined.empty else values
    # The rule of thumb is meant for three-hourly Kp, so the minute-wise estimates are combined into hourly medians
    return joined.resample(KP_STEP).median().dropna() if not joined.empty else joined


def build_aurora(panel: AuroraPanel, reader: DashboardReader, context: PanelContext) -> AuroraPayload:
    """Builds the JSON document of the aurora view from the stored maps and the Kp values of their span.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export, with the feed interval of each dataset.

    Returns:
        The payload.
    """
    maps = reader.recent(panel.dataset, panel.history, MAP_FIELDS)
    frames = []
    if not maps.empty and FRAME_FILE_FIELD in maps.columns:
        stored_maps = maps.dropna(subset=[FRAME_FILE_FIELD])
        for moment, (_, row) in zip(pd.DatetimeIndex(stored_maps.index), stored_maps.iterrows(), strict=True):
            latitude, longitude = finite_number(row.get(SUBSOLAR_LATITUDE_FIELD)), finite_number(row.get(SUBSOLAR_LONGITUDE_FIELD))
            # Without the subsolar point the frontend cannot place day and night on the map
            if latitude is None or longitude is None:
                continue
            frames.append(
                AuroraFrame(
                    time=moment.to_pydatetime(),
                    url=frame_url(panel.dataset, str(row[FRAME_FILE_FIELD])),
                    edge_north=finite_number(row.get(EDGE_NORTH_FIELD)),
                    edge_south=finite_number(row.get(EDGE_SOUTH_FIELD)),
                    subsolar_latitude=latitude,
                    subsolar_longitude=longitude,
                )
            )
    start = pd.Timestamp(context.now) - pd.Timedelta(panel.history)
    kp = _kp(panel, reader, start)
    edges = KP_EDGE.compute(kp)
    freshness = []
    # A map is valid for a time ahead of its computation, so a newest map after now counts as current now
    if frames:
        freshness.append(
            dataset_freshness(
                panel.dataset, min(frames[-1].time, context.now), reader.metadata(panel.dataset), context.feed_intervals.get(panel.dataset)
            )
        )
    return AuroraPayload(
        type=panel.type,
        title=panel.title,
        start=start.to_pydatetime(),
        frames=frames,
        kp_times=index_times(kp),
        kp_values=kp.round(2).tolist(),
        kp_edges=pd.Series(edges).round(2).tolist(),
        kp_rule=f"{KP_EDGE.formula} ({KP_EDGE.reference})",
        pole_latitude=DIPOLE_POLE_LATITUDE,
        pole_longitude=DIPOLE_POLE_LONGITUDE,
        night_hours=(NIGHT_START_HOUR, NIGHT_END_HOUR),
        freshness=freshness,
    )
