import json
import logging
import pathlib
from datetime import datetime

import numpy as np
import pandas as pd
import requests
from PIL import Image

from pipeline_service.pipelines.timestamped_files import timestamped_name
from space_weather_analysis.clients.download import download_file
from space_weather_analysis.clients.image_sequences import CORONAL_HOLES_FIELD, DISK_FIELDS, SOLAR_RADIUS_ARCSEC, Frame, ImageProduct
from space_weather_analysis.solar.coronal_holes import find_coronal_holes
from space_weather_analysis.solar.geometry import solar_b0
from space_weather_analysis.solar.image_analysis import difference_image, solar_disk
from space_weather_analysis.util.atomic_files import atomic_file

logger = logging.getLogger(__name__)

# Connect timeout and longest wait for the next chunk of an image download, in seconds
IMAGE_TIMEOUT = (10.0, 60.0)
# Quality of the stored JPEG frames, from 1 to 95
JPEG_QUALITY = 80
# Centre of the solar disk in a Helioviewer image as share of its width and height, as Helioviewer centres the Sun
HELIOVIEWER_DISK_CENTRE = 0.5


def frame_file_name(time: datetime) -> str:
    """Names the stored file of a frame by its UTC time."""
    return timestamped_name(time, ".jpg")


def zoom_frame_file_name(time: datetime, image_scale: float, centre: tuple[float, float]) -> str:
    """Names the stored file of a zoom frame by its UTC time, scale and centre in arcseconds."""
    # Scale and centre in the name make a zoom onto another place fetch its frames anew instead of reusing old files
    return timestamped_name(time, f"_{image_scale:g}_{centre[0]:.0f}_{centre[1]:.0f}.jpg")


def model_frame_file_name(run_start: datetime, url: str) -> str:
    """Names the stored frame of a model run by the first frame time of its run and the image name in the frame list."""
    # The run start tells the runs apart, as a provider may reuse the image names for every run; the name ends in .jpg
    # whatever the provider format, as every frame is stored as JPEG and the cleanup removes only .jpg files
    image_name = pathlib.PurePosixPath(url.rsplit("/", 1)[-1]).with_suffix(".jpg").name
    return timestamped_name(run_start, f"_{image_name}")


def difference_file_name(frame_name: str) -> str:
    """Names the stored difference image of a zoom frame after the frame."""
    return frame_name.removesuffix(".jpg") + "_difference.jpg"


def remove_files_except(directory: pathlib.Path, kept: set[str]) -> None:
    """Deletes the stored JPEG files of a directory whose names are not kept."""
    for path in directory.glob("*.jpg"):
        if path.name not in kept:
            path.unlink()


def store_differences(directory: pathlib.Path, names: list[str], lag: int) -> list[str | None]:
    """Stores the difference image of each frame against the frame lag positions earlier, the first frames against the first.

    Args:
        directory: The directory holding the frames of the dataset.
        names: The file names of the stored frames in time order.
        lag: The number of positions between a frame and the frame it is compared with.

    Returns:
        The file name of the difference image of each frame; None where it could not be made.
    """
    differences: list[str | None] = []
    for position, name in enumerate(names):
        target = directory / difference_file_name(name)
        # A difference image is made once; the frames it compares do not change
        if not target.exists():
            try:
                # Both frames as grey levels, the earlier one lag positions back or the first frame
                with Image.open(directory / names[max(0, position - lag)]) as before, Image.open(directory / name) as after:
                    change = difference_image(np.asarray(before.convert("L")), np.asarray(after.convert("L")))
                with atomic_file(target) as difference_file:
                    Image.fromarray(change).save(difference_file, "JPEG", quality=JPEG_QUALITY)
            except OSError as error:
                logger.warning("Difference image of %s not stored: %s", name, error)
                differences.append(None)
                continue
        differences.append(target.name)
    return differences


def disk_columns(product: ImageProduct, paths: list[pathlib.Path], times: list[datetime]) -> dict[str, list[object]]:
    """Finds the solar disk of each stored frame, and its coronal holes where the product asks for them.

    Args:
        product: The image product of the frames.
        paths: The files of the frames.
        times: The times of the frames.

    Returns:
        The columns of the disk fields, and of the coronal holes where asked for, one value per frame.
    """
    disks: list[tuple[float | None, float | None, float | None]] = []
    for path in paths:
        # Helioviewer centres the Sun and renders it at a known scale, so its disk follows from the scale without measuring
        if product.helioviewer is not None:
            disks.append((HELIOVIEWER_DISK_CENTRE, HELIOVIEWER_DISK_CENTRE, SOLAR_RADIUS_ARCSEC / product.helioviewer.image_scale / product.size))
            continue
        try:
            disks.append(solar_disk(path))
        except (OSError, ValueError) as error:
            logger.warning("Solar disk of %s not measured: %s", path, error)
            disks.append((None, None, None))
    # One column per disk field, from the centre x, centre y and radius of each disk
    columns: dict[str, list[object]] = {}
    for position, field in enumerate(DISK_FIELDS):
        columns[field] = [disk[position] for disk in disks]
    if product.coronal_holes:
        # The tilt of the solar axis at each frame time places the holes in heliographic coordinates
        tilts = solar_b0(pd.DatetimeIndex(times))
        columns[CORONAL_HOLES_FIELD] = [_coronal_holes(path, disk, float(tilt)) for path, disk, tilt in zip(paths, disks, tilts, strict=True)]
    return columns


def _coronal_holes(path: pathlib.Path, disk: tuple[float | None, float | None, float | None], tilt: float) -> str | None:
    """Finds the coronal holes of one frame as JSON text, stored as one field.

    Args:
        path: The file of the frame.
        disk: The centre x and y and the radius of the solar disk as shares of the image; None where not measured.
        tilt: The tilt of the solar axis towards the observer (B0 angle) in degrees.

    Returns:
        The coronal holes as JSON text; None if the disk is unknown or the frame cannot be read.
    """
    if disk[0] is None or disk[1] is None or disk[2] is None:
        return None
    try:
        holes = find_coronal_holes(path, (disk[0], disk[1], disk[2]), tilt)
    except OSError as error:
        logger.warning("Coronal holes of %s not found: %s", path, error)
        return None
    return json.dumps([hole.model_dump() for hole in holes], separators=(",", ":"))


def store_frame(session: requests.Session, frame: Frame, path: pathlib.Path, size: int) -> None:
    """Downloads one frame and stores it scaled to the given length of its longer side as JPEG.

    Args:
        session: The HTTP session for the download.
        frame: The frame to download.
        path: The file to store the frame in.
        size: The length of the longer side of the stored frame in pixels.

    Raises:
        DataRetrievalError: If the download fails.
        OSError: If the image cannot be read or written.
    """
    # The original goes to a file of its own next to the frame and is removed whether the scaling succeeds or not
    original = path.with_suffix(".download")
    try:
        download_file(session, frame.url, original, IMAGE_TIMEOUT, "Frame")
        with Image.open(original) as image:
            # JPEG holds no transparency or palette, so every image is turned into RGB first
            scaled = image.convert("RGB")
            # Shrinks in place keeping the aspect ratio, so the longer side has size pixels; a smaller image stays
            scaled.thumbnail((size, size))
            # Written through a temporary file, so the site export never copies a half written frame
            with atomic_file(path) as target:
                scaled.save(target, "JPEG", quality=JPEG_QUALITY)
    finally:
        original.unlink(missing_ok=True)
