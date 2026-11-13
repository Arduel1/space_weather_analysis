import pathlib

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

# Share of the brightest pixel value above which a pixel belongs to the solar disk; the sky around it is black, and
# even the dark core of a sunspot stays above it
DISK_THRESHOLD = 0.1
# Width in pixels of the Gaussian that smooths both frames of a difference image against the noise of single pixels
DIFFERENCE_SMOOTHING = 2.0
# Change in grey levels that reaches white or black in a difference image
DIFFERENCE_SCALE = 12.0
# Half the range of 8-bit grey values, which maps a change from -1 to 1 onto 0 to 255
GREY_HALF_RANGE = np.iinfo(np.uint8).max / 2


def _bright_run(bright: np.ndarray, centre: int) -> tuple[int, int]:
    """Returns the first and last index of the run of bright values around a centre index; the centre itself if it is dark."""
    if not bright[centre]:
        return centre, centre
    dark = np.flatnonzero(~bright)
    before = dark[dark < centre]
    after = dark[dark > centre]
    return (int(before[-1]) + 1 if before.size else 0), (int(after[0]) - 1 if after.size else len(bright) - 1)


def solar_disk(path: pathlib.Path) -> tuple[float, float, float]:
    """Measures the solar disk in an image of the whole Sun: its centre and radius as shares of the image width and height.

    Args:
        path: The image file.

    Returns:
        The centre x and y as shares of the width and height from the top left, and the radius as share of the width.

    Raises:
        OSError: If the image cannot be read.
        ValueError: If the image centre is not on a bright disk.
    """
    with Image.open(path) as image:
        brightness = np.asarray(image.convert("L"), dtype=float)
    # The disk is the bright area holding the image centre; labels beside it are cut off by the dark sky between
    bright = brightness > brightness.max() * DISK_THRESHOLD
    height, width = brightness.shape
    centre_x, centre_y = width // 2, height // 2
    if not bright[centre_y, centre_x]:
        raise ValueError(f"The centre of {path} is not on a bright disk.")
    # The run of bright pixels along the row and column through the centre spans the disk; the second pass repeats the
    # measurement through the centre of the first, which lies closer to the true centre for an off-centre disk
    for _ in range(2):
        left, right = _bright_run(bright[centre_y, :], centre_x)
        top, bottom = _bright_run(bright[:, centre_x], centre_y)
        centre_x, centre_y = (left + right) // 2, (top + bottom) // 2
    # The radius averages the horizontal and vertical diameter; the centre lies half a pixel inside the last bright pixel
    radius = (right - left + 1 + bottom - top + 1) / 4
    return (left + right + 1) / 2 / width, (top + bottom + 1) / 2 / height, radius / width


def difference_image(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    """Shows how a frame changed since an earlier one as grey image: mid grey unchanged, brighter white, fainter black.

    Args:
        before: The brightness of the earlier frame, one value per pixel.
        after: The brightness of the later frame.

    Returns:
        The difference image as 8-bit grey values.
    """
    change = gaussian_filter(after.astype(float), DIFFERENCE_SMOOTHING) - gaussian_filter(before.astype(float), DIFFERENCE_SMOOTHING)
    # The change scaled to -1 to 1 and clipped, then shifted to 0 to 2 and stretched to the grey levels 0 to 255
    return np.round((np.clip(change / DIFFERENCE_SCALE, -1, 1) + 1) * GREY_HALF_RANGE).astype(np.uint8)
