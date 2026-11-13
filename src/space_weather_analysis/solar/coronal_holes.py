import pathlib

import contourpy
import numpy as np
from PIL import Image, ImageFilter
from pydantic import BaseModel, ConfigDict
from scipy import ndimage

from space_weather_analysis.solar.geometry import heliographic_position, thinned_line

# Coronal holes, regions of open magnetic field where fast solar wind escapes, are the regions of the disk darker than
# this share of the median brightness of the disk: the threshold method of Rotter et al. (2012) applied to the rendered
# images. The share is chosen on images of the 193 angstrom channel of AIA (Atmospheric Imaging Assembly on the Solar
# Dynamics Observatory), whose brightness is not linear in the emission
DARK_SHARE = 0.4
# Share of the solar radius searched; near the limb the corona of the far side brightens the disk
SEARCHED_RADIUS = 0.95
# Smallest coronal hole as share of the searched disk area; smaller dark patches are filaments or noise
MIN_AREA_SHARE = 0.002
# Width of the median filter in pixels, which removes single bright or dark pixels before the threshold
MEDIAN_WIDTH = 5
# Passes of the morphological opening (erosion followed by dilation) that remove thin dark lanes
OPENING_PASSES = 2
# Most points kept of an outline
OUTLINE_POINTS = 60

# Source of the detection method, shown with the solar disk
CORONAL_HOLE_REFERENCE = (
    "Rotter, T., Veronig, A. M., Temmer, M. and Vrsnak, B. (2012), Relation Between Coronal Hole Areas on the Sun and "
    "the Solar Wind Parameters at 1 AU, Solar Physics 281, 793, doi:10.1007/s11207-012-0101-y"
)


class CoronalHole(BaseModel):
    """A coronal hole found in an image of the whole Sun.

    Attributes:
        outline: The border as x and y shares of the image width and height from the top left.
        latitude: The heliographic latitude of its centre in degrees.
        longitude: The longitude of its centre from the central meridian in degrees, west positive.
        area_share: Its area as share of the searched disk area seen in the image.
    """

    model_config = ConfigDict(frozen=True)

    outline: list[tuple[float, float]]
    latitude: float
    longitude: float
    area_share: float


def _outline(hole: np.ndarray, width: int, height: int) -> list[tuple[float, float]]:
    """Traces the outer border of a hole mask, thinned to at most OUTLINE_POINTS points.

    Args:
        hole: The mask of the hole over the image pixels.
        width: The image width in pixels.
        height: The image height in pixels.

    Returns:
        The border points as x and y shares of the image width and height from the top left.
    """
    # The outline of a hole is the half-way line of its mask; the longest line is its outer border
    lines = [np.asarray(line) for line in contourpy.contour_generator(z=hole.astype(float)).lines(0.5)]
    outline = thinned_line(max(lines, key=lambda line: line.shape[0]), OUTLINE_POINTS)
    # The points count in pixel indices; the centre of pixel 0 lies half a pixel from the image edge
    return [(round(float(x + 0.5) / width, 4), round(float(y + 0.5) / height, 4)) for x, y in outline]


def find_coronal_holes(path: pathlib.Path, disk: tuple[float, float, float], b0: float) -> list[CoronalHole]:
    """Finds the coronal holes in an extreme ultraviolet image of the whole Sun.

    Args:
        path: The image file.
        disk: The disk centre x and y as shares of the width and height, and the radius as share of the width.
        b0: The B0 angle, the tilt of the solar axis towards Earth, in degrees at the time of the image.

    Returns:
        The coronal holes, largest first.

    Raises:
        OSError: If the image cannot be read.
    """
    with Image.open(path) as image:
        brightness = np.asarray(image.convert("L").filter(ImageFilter.MedianFilter(MEDIAN_WIDTH)), dtype=float)
    height, width = brightness.shape
    centre_x, centre_y, radius = disk[0] * width, disk[1] * height, disk[2] * width
    # The searched disk: the pixels whose centre lies within SEARCHED_RADIUS of the solar radius from the disk centre
    rows, columns = np.mgrid[0:height, 0:width]
    searched = np.hypot(columns + 0.5 - centre_x, rows + 0.5 - centre_y) < SEARCHED_RADIUS * radius
    if not searched.any():
        return []
    # Candidates are the searched pixels darker than DARK_SHARE of the median of the disk, cleared of thin dark lanes
    dark = ndimage.binary_opening(searched & (brightness < DARK_SHARE * np.median(brightness[searched])), iterations=OPENING_PASSES)
    # Each connected region of candidates gets a number from 1 to count
    labels, count = ndimage.label(dark)
    holes = []
    for label in range(1, count + 1):
        hole = labels == label
        area_share = float(hole.sum() / searched.sum())
        if area_share < MIN_AREA_SHARE:
            continue
        # The centre of the region in shares of the solar radius west and north of the disk centre, image rows counting down
        row, column = ndimage.center_of_mass(hole)
        latitude, longitude = heliographic_position(
            np.array([(column + 0.5 - centre_x) / radius]), np.array([(centre_y - row - 0.5) / radius]), np.array([b0])
        )
        holes.append(
            CoronalHole(
                outline=_outline(hole, width, height),
                latitude=round(float(latitude[0]), 1),
                longitude=round(float(longitude[0]), 1),
                area_share=round(area_share, 4),
            )
        )
    return sorted(holes, key=lambda hole: hole.area_share, reverse=True)
