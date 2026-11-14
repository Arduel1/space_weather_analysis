from datetime import datetime

import astropy.constants as const
import astropy.units as u
import numpy as np
import sunpy.map
from astropy.coordinates import SkyCoord
from pydantic import BaseModel, ConfigDict
from sunkit_magex import pfss
from sunkit_magex.pfss import tracing
from sunpy.coordinates import sun

from space_weather_analysis.solar.geometry import thinned_line
from space_weather_analysis.solar.synoptic_maps import Magnetogram

# The PFSS model (potential field source surface) takes the coronal field as free of currents between the photosphere
# and a sphere, the source surface, beyond which the solar wind has drawn all field lines out radially. Radial grid
# points between photosphere and source surface, as the sunkit-magex examples for GONG maps (Global Oscillation Network
# Group of the US National Solar Observatory) use them
RADIAL_POINTS = 35
# Radius of the source surface in solar radii, the usual value of the PFSS model
SOURCE_SURFACE_RADIUS = 2.5
# Seeds of the drawn field lines: a grid on the surface at this radius, spaced in degrees of latitude and longitude
SEED_RADIUS = 1.05
# Latitudes and longitudes of the seed grid in degrees
SEED_LATITUDES = np.arange(-78.0, 79.0, 12.0)
SEED_LONGITUDES = np.arange(0.0, 360.0, 15.0)
# Grid of the open field map in degrees; each cell is traced from just above the surface
OPEN_MAP_STEP = 4.0
# Radius in solar radii from which each cell of the open field map is traced
OPEN_MAP_RADIUS = 1.01
# Most points kept of a drawn field line; the shape of a line needs no more, and the models of a day are loaded together
LINE_POINTS = 30
# Steps the tracer may take along one line; the automatic limit stops some long closed lines before their end
TRACER_STEPS = 20000
# Cells of the surface maps combined to one along latitude and longitude, halving the GONG grid
MAP_BINNING = 2

# Publications of the model and the software, shown with the field model
PFSS_REFERENCE = (
    "Altschuler, M. D. and Newkirk, G. (1969), Solar Physics 9, 131, doi:10.1007/BF00145734; "
    "Schatten, K. H., Wilcox, J. M. and Ness, N. F. (1969), Solar Physics 6, 442, doi:10.1007/BF00146478; "
    "computed with sunkit-magex, the successor of pfsspy: Stansby, D., Yeates, A. and Badman, S. (2020), "
    "J. Open Source Softw. 5, 2732, doi:10.21105/joss.02732"
)

# Models are frozen, as they are only built and then serialized
FROZEN = ConfigDict(frozen=True)


class FieldLine(BaseModel):
    """One traced magnetic field line.

    Attributes:
        points: The points of the line as x, y, z in solar radii in the Carrington frame, rounded to 2 digits. The
            Carrington frame turns with the Sun at the sidereal period of 25.38 days that Carrington determined
            (Thompson 2006, Astronomy & Astrophysics 449, 791, doi:10.1051/0004-6361:20054262).
        open: Whether the line reaches the source surface, so that it is open to interplanetary space.
        polarity: The sign of the field at the photospheric end of an open line, 0 for a closed line.
    """

    model_config = FROZEN

    points: list[tuple[float, float, float]]
    open: bool
    polarity: int


class SurfaceMap(BaseModel):
    """A map over Carrington longitude and latitude.

    Attributes:
        longitudes: The Carrington longitudes of the columns in degrees.
        latitudes: The latitudes of the rows in degrees, from south to north.
        values: One row per latitude, one value per longitude.
    """

    model_config = FROZEN

    longitudes: list[float]
    latitudes: list[float]
    values: list[list[float]]


class FieldModel(BaseModel):
    """The PFSS model of the coronal magnetic field computed from one synoptic magnetogram.

    A synoptic magnetogram maps the radial field of the photosphere over a whole solar rotation in Carrington coordinates.

    Attributes:
        map_time: The time of the synoptic map.
        source_surface_radius: The radius of the source surface in solar radii.
        earth_longitude: The Carrington longitude of the disk centre seen from Earth at the map time.
        lines: The field lines traced from a regular grid of seeds.
        surface_field: The radial field at the photosphere in gauss.
        source_surface_field: The radial field at the source surface in gauss, whose zero line is the heliospheric
            current sheet.
        open_field: Per cell of a coarser grid, the polarity of the open field starting there, 0 where the field is closed.
        reference: The publications of the model and of the software.
        magnetogram: The instrument whose synoptic magnetogram the model is computed from.
    """

    model_config = FROZEN

    map_time: datetime
    source_surface_radius: float
    earth_longitude: float
    lines: list[FieldLine]
    surface_field: SurfaceMap
    source_surface_field: SurfaceMap
    open_field: SurfaceMap
    reference: str
    magnetogram: Magnetogram


def _binned(values: np.ndarray, factor: int) -> np.ndarray:
    """Averages blocks of `{factor}` by `{factor}` cells of a map, dropping the rows and columns left over."""
    rows, columns = values.shape[0] // factor * factor, values.shape[1] // factor * factor
    # Each block becomes its own two axes of the reshaped array, whose mean over them is the block average
    return values[:rows, :columns].reshape(rows // factor, factor, columns // factor, factor).mean(axis=(1, 3))


def _surface_map(values: np.ndarray) -> SurfaceMap:
    """Turns a map on the sine latitude grid of the model into a map over longitude and latitude, binned by MAP_BINNING."""
    binned = _binned(values, MAP_BINNING)
    rows, columns = binned.shape
    # The cell centres: longitudes evenly over 360 degrees, latitudes evenly in their sine from -1 to 1 as on the model grid
    longitudes = (np.arange(columns) + 0.5) * 360.0 / columns
    latitudes = np.degrees(np.arcsin(-1 + (np.arange(rows) + 0.5) * 2.0 / rows))
    return SurfaceMap(
        longitudes=np.round(longitudes, 2).tolist(),
        latitudes=np.round(latitudes, 2).tolist(),
        values=np.round(binned, 2).tolist(),
    )


def _seeds(latitudes: np.ndarray, longitudes: np.ndarray, radius: float, frame: object) -> SkyCoord:
    """Builds seeds on a grid of latitudes and longitudes at a radius in solar radii."""
    longitude_grid, latitude_grid = np.meshgrid(longitudes, latitudes)
    return SkyCoord(longitude_grid.ravel() * u.deg, latitude_grid.ravel() * u.deg, radius * const.R_sun, frame=frame)


def _field_lines(tracer: tracing.PerformanceTracer, output: pfss.Output) -> list[FieldLine]:
    """Traces the drawn field lines from the seed grid, skipping lines of fewer than two points.

    Args:
        tracer: The field line tracer.
        output: The computed model.

    Returns:
        The thinned field lines in the order of the seeds.
    """
    lines = []
    for line in tracer.trace(_seeds(SEED_LATITUDES, SEED_LONGITUDES, SEED_RADIUS, output.coordinate_frame), output):
        coordinates = line.coords
        if len(coordinates) < 2:
            continue
        # The points as rows of x, y and z in solar radii
        points = thinned_line((coordinates.cartesian.xyz / const.R_sun).decompose().value.T, LINE_POINTS)
        lines.append(FieldLine(points=[tuple(point) for point in np.round(points, 2).tolist()], open=bool(line.is_open), polarity=int(line.polarity)))
    return lines


def _open_field_map(tracer: tracing.PerformanceTracer, output: pfss.Output) -> SurfaceMap:
    """Traces one line from just above the surface per cell of a grid and maps the polarity of the open ones.

    Args:
        tracer: The field line tracer.
        output: The computed model.

    Returns:
        The polarity per cell, 0 where the field is closed.
    """
    # The cell centres of the grid
    map_latitudes = np.arange(-90 + OPEN_MAP_STEP / 2, 90, OPEN_MAP_STEP)
    map_longitudes = np.arange(OPEN_MAP_STEP / 2, 360, OPEN_MAP_STEP)
    traced = tracer.trace(_seeds(map_latitudes, map_longitudes, OPEN_MAP_RADIUS, output.coordinate_frame), output)
    # The seeds run longitude fastest, so the polarities fold into one row per latitude; a closed line has polarity 0
    polarities = np.asarray(traced.polarities, dtype=float).reshape(len(map_latitudes), len(map_longitudes))
    return SurfaceMap(longitudes=map_longitudes.tolist(), latitudes=map_latitudes.tolist(), values=polarities.tolist())


def compute_field(synoptic: sunpy.map.GenericMap, magnetogram: Magnetogram) -> FieldModel:
    """Computes the PFSS model of the coronal magnetic field from a synoptic magnetogram.

    Args:
        synoptic: The complete synoptic map of the radial field in Carrington coordinates and sine latitude.
        magnetogram: The instrument the map comes from.

    Returns:
        The model with traced field lines and maps.

    Raises:
        ValueError: If the map is no synoptic map the model accepts.
    """
    # Solves for the potential field between the photosphere and the source surface
    model_input = pfss.Input(synoptic, RADIAL_POINTS, SOURCE_SURFACE_RADIUS)
    output = pfss.pfss(model_input)
    tracer = tracing.PerformanceTracer(max_steps=TRACER_STEPS)
    lines = _field_lines(tracer, output)
    open_field = _open_field_map(tracer, output)
    return FieldModel(
        map_time=synoptic.date.to_datetime(),
        source_surface_radius=SOURCE_SURFACE_RADIUS,
        # L0, the Carrington longitude facing Earth, places Earth on the maps
        earth_longitude=float(sun.L0(synoptic.date).to_value(u.deg)),
        lines=lines,
        surface_field=_surface_map(np.asarray(model_input.map.data, dtype=float)),
        source_surface_field=_surface_map(np.asarray(output.source_surface_br.data, dtype=float)),
        open_field=open_field,
        reference=PFSS_REFERENCE,
        magnetogram=magnetogram,
    )
