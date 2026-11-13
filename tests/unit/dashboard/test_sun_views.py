import pathlib
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
from PIL import Image

from space_weather_analysis.clients.image_sequences import HelioviewerSource, ImageProduct, helioviewer_frames, load_image_products
from space_weather_analysis.dashboard.panels.butterfly import butterfly_classes
from space_weather_analysis.solar.coronal_holes import find_coronal_holes
from space_weather_analysis.solar.geometry import disk_position, heliographic_position

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]


def test_the_project_image_products_are_valid() -> None:
    """The image products of the project load, so that no invalid entry stops the fetching of all image sequences."""
    products = load_image_products(PROJECT_ROOT / "config" / "sources" / "images.toml")

    assert {"sdo_aia_193", "sdo_aia_flare_zoom"} <= products.keys()


def test_helioviewer_frames_lie_on_the_spacing_grid_and_end_before_the_delay() -> None:
    """The frames of a Helioviewer product cover the span on the grid of the spacing, the newest one before the delay."""
    product = ImageProduct.model_validate({"spacing": "PT12M", "helioviewer": {"layers": "[SDO,AIA,AIA,193,1,100]", "image_scale": 4.8}})
    assert product.helioviewer is not None

    frames = helioviewer_frames(product, product.helioviewer, datetime(2026, 11, 8, 12, 5, tzinfo=UTC), timedelta(hours=1))

    assert ([frame.time.strftime("%H:%M") for frame in frames], "date=2026-11-08T11%3A24%3A00Z" in frames[-1].url) == (
        ["10:24", "10:36", "10:48", "11:00", "11:12", "11:24"],
        True,
    )


def test_a_helioviewer_view_centred_south_of_the_disk_centre_asks_for_a_positive_y() -> None:
    """Helioviewer counts y downwards in the image, so a centre in the south needs a positive y0 and west a positive x0."""
    source = HelioviewerSource(layers="[SDO,AIA,AIA,131,1,100]", image_scale=1.8)

    url = source.url(datetime(2024, 1, 1, tzinfo=UTC), 512, centre=(50.0, -115.0))

    assert "x0=50.0" in url and "y0=115.0" in url


def test_a_position_on_the_disk_projects_back_to_itself() -> None:
    """The inverse projection returns the latitude and longitude a visible point was projected from."""
    latitude, longitude, tilt = np.array([20.0, -35.0]), np.array([-40.0, 60.0]), np.array([6.0, 6.0])
    west, north, _ = disk_position(latitude, longitude, tilt)

    back = heliographic_position(west, north, tilt)

    assert np.round(back, 6).tolist() == [latitude.tolist(), longitude.tolist()]


def test_butterfly_cells_class_the_share_of_each_strip_covered() -> None:
    """A large region reaches the highest class, a small one and one of unknown area the lowest; empty cells stay 0."""
    start = pd.Timestamp("2024-01-01", tz="UTC")
    times = pd.DatetimeIndex([start + pd.Timedelta(days=1), start + pd.Timedelta(days=30), start + pd.Timedelta(days=58)])
    # Strip 10 to 12 degrees covers about 1.06e11 km2; a mean of 1 % over the 27.3 day rotation needs 2.9e10 km2 a day
    areas = np.array([1.0e12, 1.0e9, np.nan])

    starts, edges, classes = butterfly_classes(times, np.array([11.0, -21.0, 31.0]), areas, start, start + pd.Timedelta(days=60))

    rows = [int(np.searchsorted(edges, latitude, side="right") - 1) for latitude in (11.0, -21.0, 31.0)]
    assert (len(starts), classes[rows[0], 0], classes[rows[1], 1], classes[rows[2], 2], int(classes.sum())) == (3, 3, 1, 1, 5)


def test_a_dark_area_on_the_disk_is_found_as_coronal_hole(tmp_path: pathlib.Path) -> None:
    """A dark patch inside a bright disk is found with its centre at the disk centre; the dark sky around the disk is not."""
    size, radius = 200, 80
    rows, columns = np.mgrid[0:size, 0:size]
    distance = np.hypot(columns + 0.5 - size / 2, rows + 0.5 - size / 2)
    image = np.where(distance < radius, 180, 0).astype(np.uint8)
    image[(np.abs(columns + 0.5 - size / 2) < 15) & (np.abs(rows + 0.5 - size / 2) < 15)] = 30
    path = tmp_path / "sun.png"
    Image.fromarray(image).save(path)

    holes = find_coronal_holes(path, (0.5, 0.5, radius / size), 0.0)

    assert (len(holes), abs(holes[0].latitude) < 2, abs(holes[0].longitude) < 2) == (1, True, True)
