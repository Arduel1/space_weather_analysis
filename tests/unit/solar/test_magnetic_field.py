from datetime import UTC, datetime

import numpy as np
import sunpy.map
from sunkit_magex.pfss.utils import carr_cea_wcs_header

from space_weather_analysis.solar.magnetic_field import compute_field
from space_weather_analysis.solar.synoptic_maps import Magnetogram


def dipole_map() -> sunpy.map.GenericMap:
    """A synthetic synoptic magnetogram of a dipole along the rotation axis, outward field in the north."""
    longitude_cells, latitude_cells = 72, 36
    # The rows are evenly spaced in sine latitude, in which the radial field of a dipole grows linearly
    sine_latitudes = -1 + (np.arange(latitude_cells) + 0.5) * 2 / latitude_cells
    field = np.repeat(sine_latitudes[:, np.newaxis], longitude_cells, axis=1)
    return sunpy.map.Map(field, carr_cea_wcs_header(datetime(2026, 1, 1, tzinfo=UTC), (longitude_cells, latitude_cells)))


def test_the_open_field_of_a_dipole_leaves_the_poles_with_their_polarity_and_is_closed_at_the_equator() -> None:
    """The open field map has outward field at the north pole, inward field at the south pole and no open field at the equator."""
    open_field = compute_field(dipole_map(), Magnetogram.GONG).open_field
    polarities = np.asarray(open_field.values)
    assert polarities.shape == (len(open_field.latitudes), len(open_field.longitudes))
    # The rows run from south to north
    equator_row = len(open_field.latitudes) // 2
    assert {
        "south pole": set(polarities[0]),
        "equator": set(polarities[equator_row]),
        "north pole": set(polarities[-1]),
    } == {"south pole": {-1.0}, "equator": {0.0}, "north pole": {1.0}}
