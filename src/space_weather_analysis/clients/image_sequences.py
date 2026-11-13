import pathlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple, Self
from urllib.parse import urlencode

import astropy.units as u
import requests
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sunpy.sun import constants as sun_constants

from space_weather_analysis.clients.download import download_json
from space_weather_analysis.util.config_files import CLOSED, load_toml
from space_weather_analysis.util.error_handling import DataProcessingError
from space_weather_analysis.util.time_utils import IsoDuration

# The data field of a stored frame document, the file name of the stored frame
FRAME_FILE_FIELD = "file"
# Fields of a frame showing the whole solar disk: its centre and radius as shares of the image width and height
DISK_FIELDS = ("disk_x", "disk_y", "disk_radius")
# Field of a frame holding its coronal holes as JSON text
CORONAL_HOLES_FIELD = "coronal_holes"
# Fields of a frame of a zoom onto an event: the headline, place, peak time and page of the event
ZOOM_EVENT_FIELDS = ("event_label", "event_location", "event_peak", "event_link")
# Field of a frame of a zoom naming the file of its difference image, which shows how the frame changed
DIFFERENCE_FILE_FIELD = "difference_file"
# Connect and read timeout of a frame list request, in seconds
LIST_TIMEOUT = (10.0, 30.0)
# Address of the screenshot service of the Helioviewer Project (ESA/NASA), which renders solar images of many
# instruments at a time, a scale and a size; https://api.helioviewer.org/docs/v2/
HELIOVIEWER_SCREENSHOT_URL = "https://api.helioviewer.org/v2/takeScreenshot/"
# Span of the frames of a Helioviewer product, as long as the frame lists of the other providers
HELIOVIEWER_SPAN = timedelta(days=1)
# Apparent radius of the photosphere seen from 1 AU in arcseconds (959.63, Allen's Astrophysical Quantities, 4th ed.);
# Helioviewer scales every image to this distance
SOLAR_RADIUS_ARCSEC = float(sun_constants.average_angular_size.to_value(u.arcsec))


class ImageSettings(BaseSettings):
    """Settings of the image sequence sources, read from environment variables `IMAGES_{field name}` in upper case.

    Attributes:
        products_file: The TOML file describing the image sequences, one table per dataset.
    """

    model_config = SettingsConfigDict(env_prefix="IMAGES_", frozen=True)

    products_file: pathlib.Path = pathlib.Path("config/sources/images.toml")


class HelioviewerSource(BaseModel):
    """Images rendered by Helioviewer on a time grid instead of a frame list of the provider.

    Attributes:
        layers: The image layer in the notation of Helioviewer, such as [SDO,AIA,AIA,193,1,100].
        image_scale: The scale of the rendered image in arcseconds per pixel; the Sun is in its centre.
        delay: The time an image takes to reach Helioviewer; the grid ends this long before now.
    """

    model_config = CLOSED

    layers: str = Field(min_length=1)
    image_scale: float = Field(gt=0)
    delay: IsoDuration = timedelta(minutes=30)

    def url(self, time: datetime, size: int, centre: tuple[float, float] = (0.0, 0.0)) -> str:
        """Builds the address of the image closest to a time, rendered square at a size in pixels.

        Args:
            time: The time of the image.
            size: The side of the square image in pixels.
            centre: The centre of the image in arcseconds west and north of the disk centre.

        Returns:
            The address of the image.
        """
        parameters = {
            "date": time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "imageScale": self.image_scale,
            "layers": self.layers,
            "x0": round(centre[0], 1),
            # Helioviewer counts y downwards in the image, which is towards solar south
            "y0": round(-centre[1], 1),
            "width": size,
            "height": size,
            "display": "true",
            "watermark": "false",
        }
        return f"{HELIOVIEWER_SCREENSHOT_URL}?{urlencode(parameters)}"


class EventZoom(BaseModel):
    """A zoom of Helioviewer images onto the place of the newest event of one kind, over the whole event.

    Attributes:
        events: The dataset of the events, with type, place on the Sun, begin, peak and end.
        event_type: The kind of event followed, such as FLR for flares.
        buffer: The time shown before the begin and after the end of the event.
        frames: The number of frames over the event; the video lasts this many frames.
        shortest_step: The shortest time between two frames, the cadence of the instrument.
    """

    model_config = CLOSED

    events: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    buffer: IsoDuration = timedelta(minutes=10)
    frames: int = Field(default=60, gt=1)
    shortest_step: IsoDuration = timedelta(seconds=12)


class ImageProduct(BaseModel):
    """A sequence of images a provider lists in a JSON frame list, each image holding one moment.

    Attributes:
        list_url: The URL of the frame list, a JSON array of objects with the image path in `url`; empty for images of
            Helioviewer.
        base_url: The URL the image paths of the list are relative to.
        time_pattern: A regular expression with one group, matched against the image path, that captures its time.
        time_format: The strptime format of the captured time, in UTC.
        helioviewer: Renders the images with Helioviewer on the grid of the spacing instead of reading a frame list.
        spacing: The shortest time between two kept frames; frames in between are skipped.
        size: The length in pixels of the longer side of a kept frame.
        keep: How long a kept frame stays stored.
        solar_disk: Whether each frame shows the whole solar disk, whose centre and radius are stored with it, so that
            positions on the Sun can be drawn onto the image: measured in the frame, or known for images of Helioviewer.
        coronal_holes: Whether the coronal holes are found in each frame, an extreme ultraviolet image of the whole Sun
            with known disk, and stored with it.
        zoom: Renders the Helioviewer images around the place of the newest event instead of the whole Sun on a grid;
            only the frames of that event are kept.
        model_run: Whether the frame list is one run of a model reaching past now, such as WSA-Enlil, whose frames a
            newer run replaces as a whole.
    """

    model_config = CLOSED

    list_url: str = ""
    base_url: str = ""
    time_pattern: str = "(.+)"
    time_format: str = "%Y"
    helioviewer: HelioviewerSource | None = None
    spacing: IsoDuration
    size: int = Field(default=512, gt=0)
    keep: IsoDuration = timedelta(days=7)
    solar_disk: bool = False
    coronal_holes: bool = False
    zoom: EventZoom | None = None
    model_run: bool = False

    @model_validator(mode="after")
    def _one_group(self) -> Self:
        """Rejects a time pattern without exactly one group, and a product with neither frame list nor Helioviewer."""
        if re.compile(self.time_pattern).groups != 1:
            raise ValueError(f"The time pattern '{self.time_pattern}' must have exactly one group.")
        if self.helioviewer is None and not (self.list_url and self.base_url):
            raise ValueError("An image product needs a frame list with base URL, or a Helioviewer source.")
        if self.zoom is not None and self.helioviewer is None:
            raise ValueError("A zoom onto an event needs a Helioviewer source.")
        if self.coronal_holes and self.helioviewer is None:
            raise ValueError("Coronal holes are found only in images of Helioviewer, whose disk is known.")
        if self.model_run and self.helioviewer is not None:
            raise ValueError("A model run is read from a frame list, not rendered by Helioviewer.")
        return self

    def frame_time(self, path: str) -> datetime | None:
        """Reads the UTC time of a frame from its path; None if the path does not hold one."""
        match = re.search(self.time_pattern, path)
        if match is None:
            return None
        try:
            return datetime.strptime(match.group(1), self.time_format).replace(tzinfo=UTC)
        except ValueError:
            return None


class Frame(NamedTuple):
    """One image of a frame list.

    Attributes:
        time: The UTC time the image shows.
        url: The full URL of the image.
    """

    time: datetime
    url: str


def helioviewer_frames(product: ImageProduct, source: HelioviewerSource, now: datetime, span: timedelta) -> list[Frame]:
    """Lists the frames of a Helioviewer product: one per step of its spacing over a span ending its delay before now.

    Args:
        product: The image product.
        source: Its Helioviewer source.
        now: The current time.
        span: The span of frames.

    Returns:
        The frames on the grid of the spacing counted from the Unix epoch, sorted by time.
    """
    step = product.spacing.total_seconds()
    # The newest grid point before the delay; Unix time rounded down to whole steps gives the same grid on every run
    newest = (now - source.delay).timestamp() // step * step
    count = int(span.total_seconds() // step)
    times = [datetime.fromtimestamp(newest - index * step, UTC) for index in range(count, -1, -1)]
    return [Frame(time, source.url(time, product.size)) for time in times]


def fetch_frames(session: requests.Session, product: ImageProduct) -> list[Frame]:
    """Reads the frame list of a product; a Helioviewer product lists the frames of the last day without a request.

    Args:
        session: The HTTP session for the request.
        product: The image product.

    Returns:
        The frames with a readable time, sorted by time.

    Raises:
        SourceUnreachableError: If the provider sent no answer.
        DataRetrievalError: If the list cannot be downloaded.
        DataProcessingError: If the list is no JSON array of objects with `url`.
    """
    if product.helioviewer is not None:
        return helioviewer_frames(product, product.helioviewer, datetime.now(UTC), HELIOVIEWER_SPAN)
    content: Any = download_json(session, product.list_url, LIST_TIMEOUT, "Frame list")
    if not isinstance(content, list):
        raise DataProcessingError(f"Frame list {product.list_url} is no array.")
    frames = []
    for entry in content:
        # An entry without a path or a time in its path is left out
        path = entry.get("url") if isinstance(entry, dict) else None
        time = product.frame_time(path) if isinstance(path, str) else None
        if path is not None and time is not None:
            frames.append(Frame(time, product.base_url.rstrip("/") + "/" + path.lstrip("/")))
    return sorted(frames)


def thin_frames(frames: list[Frame], spacing: timedelta) -> list[Frame]:
    """Keeps the first frame of every step of the spacing, counted on a grid from the Unix epoch.

    A grid instead of the distance to the previous kept frame keeps the same frames on every run, whatever the first
    frame of the list is.

    Args:
        frames: The frames sorted by time.
        spacing: The step of the grid.

    Returns:
        The kept frames, sorted by time.
    """
    kept: dict[int, Frame] = {}
    step = spacing.total_seconds()
    for frame in frames:
        # The number of the grid step a frame falls into; the first frame of each step is kept
        kept.setdefault(int(frame.time.timestamp() // step), frame)
    return list(kept.values())


class _ProductsFile(BaseModel):
    """The content of the image products file: one table per dataset under `product`."""

    model_config = CLOSED

    product: dict[str, ImageProduct] = Field(default_factory=dict)


def load_image_products(path: pathlib.Path) -> dict[str, ImageProduct]:
    """Reads the image products file.

    Args:
        path: The path of the TOML file.

    Returns:
        The products by dataset ID.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is no valid TOML.
        pydantic.ValidationError: If a product is invalid.
    """
    return load_toml(path, _ProductsFile).product


def configured_image_products() -> dict[str, ImageProduct]:
    """Reads the image products file of the settings.

    Returns:
        The products by dataset ID.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is no valid TOML.
        pydantic.ValidationError: If a product or a setting is invalid.
    """
    return load_image_products(ImageSettings().products_file)
