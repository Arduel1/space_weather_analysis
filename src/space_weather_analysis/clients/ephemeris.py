import logging
import pathlib
import re
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Literal, Self
from urllib.parse import urlencode

import astropy.units as u
import numpy as np
import pandas as pd
import requests
from astropy.coordinates import CartesianRepresentation, HeliocentricEclipticIAU76, SkyCoord
from astropy.time import Time
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sunpy.coordinates import HeliographicStonyhurst, get_body_heliographic_stonyhurst

from space_weather_analysis.clients.donki import EARTH
from space_weather_analysis.clients.download import download_json
from space_weather_analysis.util.config_files import CLOSED, load_toml
from space_weather_analysis.util.error_handling import DataProcessingError, DataRetrievalError

logger = logging.getLogger(__name__)

# JPL Horizons, the ephemeris service of the NASA Jet Propulsion Laboratory, computes the positions of spacecraft from
# their navigation solutions (https://ssd-api.jpl.nasa.gov/doc/horizons.html); its data are public domain and JPL asks
# for this page as citation
HORIZONS_PAGE_URL = "https://ssd.jpl.nasa.gov/horizons/"
HORIZONS_API_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
# Connect and read timeout in seconds
HORIZONS_TIMEOUT = (10.0, 60.0)
# One position of the vector table of Horizons in its text layout, such as " X = 3.27E-02 Y = 9.66E-01 Z =-1.78E-03":
# each coordinate is its letter, an equals sign with optional spaces around it (\s*), and its number as group (\S+, all
# characters up to the next space); \s+ requires a space before Y and Z, as a negative number follows "=" directly
POSITION_LINE = re.compile(r"X\s*=\s*(\S+)\s+Y\s*=\s*(\S+)\s+Z\s*=\s*(\S+)")

# Stored fields of a position: the body as DONKI names it, whether it is a planet or a spacecraft, and its place in
# heliographic Stonyhurst coordinates, whose longitude counts from the Sun-Earth line (degrees, west of the Sun as
# seen from Earth positive), with latitude in degrees and distance from the Sun in astronomical units
BODY_FIELD = "body"
KIND_FIELD = "kind"
LONGITUDE_FIELD = "longitude"
LATITUDE_FIELD = "latitude"
DISTANCE_FIELD = "distance"
POSITION_FIELDS = (BODY_FIELD, KIND_FIELD, LONGITUDE_FIELD, LATITUDE_FIELD, DISTANCE_FIELD)


class EphemerisSettings(BaseSettings):
    """Settings of the ephemeris source, read from environment variables `EPHEMERIS_{field name}` in upper case.

    Attributes:
        bodies_file: The TOML file listing the planets and spacecraft whose positions are stored.
    """

    model_config = SettingsConfigDict(env_prefix="EPHEMERIS_", frozen=True)

    bodies_file: pathlib.Path = pathlib.Path("config/sources/bodies.toml")


class Body(BaseModel):
    """A planet or spacecraft whose position is stored.

    Attributes:
        kind: Planets come from the planetary ephemeris built into astropy without a request, spacecraft from JPL Horizons.
        ephemeris_id: The name of a planet for astropy, or the Horizons ID of a spacecraft such as -234 for STEREO A.
    """

    model_config = CLOSED

    kind: Literal["planet", "spacecraft"]
    ephemeris_id: str = Field(min_length=1)


class _BodiesFile(BaseModel):
    """The content of the bodies file: one table per body under `body`, keyed by the name DONKI gives it."""

    model_config = CLOSED

    body: dict[str, Body] = Field(min_length=1)

    @model_validator(mode="after")
    def _earth_listed(self) -> Self:
        """Rejects a list without Earth, from which every view of the bodies is oriented."""
        if EARTH not in self.body:
            raise ValueError("The bodies file must list Earth.")
        return self


def load_bodies(path: pathlib.Path) -> dict[str, Body]:
    """Reads the bodies file.

    Args:
        path: The path of the TOML file.

    Returns:
        The bodies by name.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is no valid TOML.
        pydantic.ValidationError: If a body is invalid.
    """
    return load_toml(path, _BodiesFile).body


def configured_bodies() -> dict[str, Body]:
    """Reads the bodies file of the settings; raises like load_bodies."""
    return load_bodies(EphemerisSettings().bodies_file)


# Returns the positions of one body at the given times as heliographic Stonyhurst coordinates
type Locator = Callable[[Body, Sequence[datetime]], SkyCoord]


def daily_times(start: datetime, stop: datetime) -> list[datetime]:
    """Lists the midnights UTC from the day of the start to the day of the stop, both included.

    Args:
        start: The start of the range, timezone-aware.
        stop: The end of the range, timezone-aware.

    Returns:
        The midnights UTC, oldest first.
    """
    first = pd.Timestamp(start).tz_convert("UTC").normalize()
    last = pd.Timestamp(stop).tz_convert("UTC").normalize()
    return [time.to_pydatetime() for time in pd.date_range(first, last, freq="1D")]


def parse_vectors(text: str, count: int) -> np.ndarray:
    """Reads the positions of the vector table of a Horizons answer in its text layout.

    Args:
        text: The answer text, whose table lies between the markers $$SOE and $$EOE.
        count: The number of positions expected.

    Returns:
        One row of X, Y and Z per position, in the units the request asked for.

    Raises:
        DataProcessingError: If the answer holds no table or another number of positions.
    """
    # $$SOE and $$EOE mark the start and end of the ephemeris table
    start, end = text.find("$$SOE"), text.find("$$EOE")
    if start < 0 or end < start:
        raise DataProcessingError(f"The Horizons answer holds no vector table: {text[:300]}")
    # Each position line gives X, Y and Z; the other lines of a table entry, such as the time, do not match
    rows = [list(map(float, match.groups())) for match in POSITION_LINE.finditer(text[start:end])]
    if len(rows) != count:
        raise DataProcessingError(f"The Horizons answer holds {len(rows)} positions instead of {count}.")
    return np.asarray(rows)


def horizons_positions(session: requests.Session, ephemeris_id: str, times: Sequence[datetime]) -> SkyCoord:
    """Fetches the positions of a spacecraft from JPL Horizons at daily times, one request for all of them.

    Args:
        session: The HTTP session for the request.
        ephemeris_id: The Horizons ID of the spacecraft.
        times: Midnights UTC one day apart, oldest first.

    Returns:
        The positions in heliographic Stonyhurst coordinates, one per time.

    Raises:
        SourceUnreachableError: If Horizons sent no answer.
        DataRetrievalError: If the request failed.
        DataProcessingError: If the answer holds no positions for the times.
    """
    # The values are quoted as the Horizons API expects; the vector table is read in its text layout, as the CSV layout
    # has dropped the vector columns at times
    parameters = {
        "format": "json",
        "COMMAND": f"'{ephemeris_id}'",
        "MAKE_EPHEM": "'YES'",
        "EPHEM_TYPE": "'VECTORS'",
        # The centre of the Sun, body 10 of Horizons
        "CENTER": "'500@10'",
        "REF_PLANE": "'ECLIPTIC'",
        "REF_SYSTEM": "'ICRF'",
        # Positions only, in astronomical units
        "VEC_TABLE": "'1'",
        "OUT_UNITS": "'AU-D'",
        # Horizons counts these times in TDB (Barycentric Dynamical Time), about a minute from UTC, which does not
        # matter for daily positions
        "START_TIME": f"'{times[0]:%Y-%m-%d %H:%M}'",
        "STOP_TIME": f"'{times[-1]:%Y-%m-%d %H:%M}'",
        "STEP_SIZE": "'1d'",
    }
    url = f"{HORIZONS_API_URL}?{urlencode(parameters)}"
    content = download_json(session, url, HORIZONS_TIMEOUT, f"Horizons positions of {ephemeris_id}")
    if not isinstance(content, dict) or not isinstance(content.get("result"), str):
        raise DataProcessingError(f"The Horizons answer for {ephemeris_id} holds no result text.")
    vectors = parse_vectors(content["result"], len(times))
    # The ecliptic of J2000 of Horizons, centred on the Sun, as astropy names it
    ecliptic = SkyCoord(
        CartesianRepresentation(vectors[:, 0], vectors[:, 1], vectors[:, 2], unit=u.AU), frame=HeliocentricEclipticIAU76, obstime=Time(list(times))
    )
    return ecliptic.transform_to(HeliographicStonyhurst)


def locate(body: Body, times: Sequence[datetime]) -> SkyCoord:
    """Computes the positions of a body at daily times: planets offline, spacecraft with one request to JPL Horizons.

    Args:
        body: The body.
        times: Midnights UTC one day apart, oldest first.

    Returns:
        The positions in heliographic Stonyhurst coordinates, one per time.

    Raises:
        DataRetrievalError: If Horizons sent no answer or the request failed.
        DataProcessingError: If Horizons holds no positions for the times.
    """
    if body.kind == "planet":
        return get_body_heliographic_stonyhurst(body.ephemeris_id, Time(list(times)))
    with requests.Session() as session:
        return horizons_positions(session, body.ephemeris_id, times)


def body_positions(bodies: dict[str, Body], times: Sequence[datetime], locator: Locator = locate) -> tuple[pd.DataFrame, list[str]]:
    """Computes the positions of every body at the times; a body whose positions fail is left out.

    Args:
        bodies: The bodies by name.
        times: The times, oldest first, at least two.
        locator: Computes the positions of one body.

    Returns:
        One row per body and time with the columns of POSITION_FIELDS and a UTC DatetimeIndex, and the names of the
        bodies left out.
    """
    frames = []
    failed = []
    for name, body in bodies.items():
        try:
            coordinates = locator(body, times)
        except (DataRetrievalError, DataProcessingError, ValueError) as error:
            logger.warning("Positions of %s not computed: %s", name, error)
            failed.append(name)
            continue
        frames.append(
            pd.DataFrame(
                {
                    BODY_FIELD: name,
                    KIND_FIELD: body.kind,
                    # Longitudes from -180 to 180 degrees, so that the bodies behind the Sun lie at the ends of the range
                    LONGITUDE_FIELD: coordinates.lon.wrap_at(180 * u.deg).to_value(u.deg),
                    LATITUDE_FIELD: coordinates.lat.to_value(u.deg),
                    DISTANCE_FIELD: coordinates.radius.to_value(u.AU),
                },
                index=pd.DatetimeIndex(list(times)),
            )
        )
    if not frames:
        return pd.DataFrame(columns=list(POSITION_FIELDS), index=pd.DatetimeIndex([], tz="UTC")), failed
    return pd.concat(frames).sort_index(kind="stable"), failed
