import pathlib
from datetime import UTC, datetime

import numpy as np
from astropy.io import fits

from space_weather_analysis.solar.synoptic_maps import fill_unobserved, hmi_synoptic_map


def test_unobserved_cells_take_the_averaged_earlier_rotation_and_observed_cells_stay() -> None:
    """A gap of the running rotation is filled with the block average of the finer earlier map, measured cells are kept."""
    current = np.array([[np.nan, 5.0], [7.0, np.nan]])
    # Twice as fine in both directions: each 2 x 2 block covers one cell of the current map
    previous = np.array([[1.0, 3.0, 9.0, 9.0], [1.0, 3.0, 9.0, 9.0], [0.0, 0.0, 4.0, 8.0], [0.0, 0.0, 4.0, 8.0]])

    filled = fill_unobserved(current, previous)

    assert filled.tolist() == [[2.0, 5.0], [7.0, 6.0]]


def test_the_hmi_map_stands_for_its_last_update_on_the_model_grid(tmp_path: pathlib.Path) -> None:
    """The built map has the cells of the GONG maps and the time of the last update, not the middle of the rotation."""
    header = {"CTYPE1": "CRLN-CEA", "CTYPE2": "CRLT-CEA", "CUNIT1": "deg", "CUNIT2": "deg", "CRVAL1": 180.0, "CRVAL2": 0.0}
    current = np.full((4, 8), 1.0)
    current[:, :2] = np.nan
    fits.writeto(
        tmp_path / "current.fits",
        current,
        fits.Header({**header, "CDELT1": 45.0, "CDELT2": 22.5, "CRPIX1": 4.5, "CRPIX2": 2.5, "T_OBS": "2026-11-05T23:13:00"}),
    )
    fits.writeto(
        tmp_path / "final.fits", np.full((8, 16), 3.0), fits.Header({**header, "CDELT1": 22.5, "CDELT2": 11.25, "CRPIX1": 8.5, "CRPIX2": 4.5})
    )
    updated = datetime(2026, 11, 13, 17, 49, 4, tzinfo=UTC)

    synoptic = hmi_synoptic_map(tmp_path / "current.fits", tmp_path / "final.fits", updated)

    assert (synoptic.data.shape, synoptic.date.isot, bool(np.isnan(synoptic.data).any())) == ((180, 360), "2026-11-13T17:49:04.000", False)
