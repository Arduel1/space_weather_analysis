import pathlib
from datetime import UTC, datetime
from enum import StrEnum

import astropy.units as u
import numpy as np
import sunpy.map

# Cells of every map the PFSS model gets, in Carrington longitude and sine latitude: the grid of the GONG maps, which
# keeps the computing time of a model the same whatever magnetogram it comes from
MODEL_GRID = (360, 180)


class Magnetogram(StrEnum):
    """The instruments whose synoptic magnetograms a PFSS model can be computed from."""

    # The hourly updated maps of the Global Oscillation Network Group of the NSF National Solar Observatory
    GONG = "GONG"
    # The daily updated maps of the Helioseismic and Magnetic Imager on the Solar Dynamics Observatory of NASA
    HMI = "SDO/HMI"


def gong_synoptic_map(path: pathlib.Path) -> sunpy.map.GenericMap:
    """Reads a GONG synoptic magnetogram, which is complete and already on MODEL_GRID.

    Raises:
        OSError: If the file cannot be read.
    """
    return sunpy.map.Map(path)


def fill_unobserved(current: np.ndarray, previous: np.ndarray) -> np.ndarray:
    """Fills the cells of a map of the running rotation that are not observed yet with the map of an earlier rotation.

    Both maps cover the same Carrington longitudes and sine latitudes; the earlier map may have a finer grid by a whole
    factor in each direction and is averaged to the grid of the current map.

    Args:
        current: The radial field of the running rotation, NaN where not observed.
        previous: The complete radial field of an earlier rotation.

    Returns:
        The current map with the gaps taken from the earlier one.

    Raises:
        ValueError: If the grid of the earlier map is no whole multiple of the current grid.
    """
    rows, columns = current.shape
    row_factor, column_factor = previous.shape[0] // rows, previous.shape[1] // columns
    if (rows * row_factor, columns * column_factor) != previous.shape:
        raise ValueError(f"A map of {previous.shape} cells cannot be averaged to {current.shape} cells.")
    # Averages each block of row_factor by column_factor cells into the one cell of the current grid it covers
    averaged = previous.reshape(rows, row_factor, columns, column_factor).mean(axis=(1, 3))
    return np.where(np.isnan(current), averaged, current)


def hmi_synoptic_map(current_path: pathlib.Path, previous_path: pathlib.Path, updated: datetime) -> sunpy.map.GenericMap:
    """Builds a complete synoptic magnetogram on MODEL_GRID from the HMI map of the running rotation.

    The near-real-time map of the running rotation holds only the longitudes observed so far and leaves out the poles,
    which HMI sees at a steep angle; both gaps are filled from the polar-filled map of an earlier rotation, the way an
    hourly updated GONG map is built.

    Args:
        current_path: The FITS file of the near-real-time map of the running rotation.
        previous_path: The FITS file of the polar-filled map of an earlier rotation.
        updated: When the near-real-time map was last updated, the time the model stands for.

    Returns:
        The map in Carrington coordinates.

    Raises:
        OSError: If a file cannot be read.
        ValueError: If the two maps do not share their grid.
    """
    current = sunpy.map.Map(current_path)
    previous = sunpy.map.Map(previous_path)
    meta = current.meta.copy()
    # The header gives the middle of the rotation as observation time; the model stands for the last update instead,
    # which places Earth on the maps. FITS times are UTC without zone suffix, and sunpy takes the current time for a
    # value it cannot read
    meta["date-obs"] = updated.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    meta.pop("t_obs", None)
    filled = sunpy.map.Map(fill_unobserved(np.asarray(current.data, dtype=float), np.asarray(previous.data, dtype=float)), meta)
    return filled.resample(list(MODEL_GRID) * u.pix)
