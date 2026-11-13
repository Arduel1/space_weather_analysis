from datetime import datetime, timedelta
from typing import Self

from pydantic import BaseModel, Field, model_validator

from space_weather_analysis.clients.image_sequences import FRAME_FILE_FIELD, ZOOM_EVENT_FIELDS
from space_weather_analysis.dashboard.freshness import DatasetFreshness, dataset_freshness
from space_weather_analysis.dashboard.labels import dataset_title
from space_weather_analysis.dashboard.panels.base import CLOSED, PAYLOAD, PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.panels.series import index_times
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.util.time_utils import IsoDuration

# Directory of the site data directory holding the media files, one subdirectory per dataset
MEDIA_DIRECTORY = "media"


class ImageVariant(BaseModel):
    """One image sequence an image panel offers, such as one wavelength of a solar imager.

    Attributes:
        dataset: The dataset ID of the image sequence, which must belong to a feed.
        label: The name of the variant on its tab.
        field: The field of the frames naming the shown file, such as the difference image of a zoom.
    """

    model_config = CLOSED

    dataset: str = Field(min_length=1)
    label: str = Field(min_length=1)
    field: str = Field(default=FRAME_FILE_FIELD, min_length=1)


class ImagesPanel(PanelConfig):
    """Image sequences played as videos, one variant at a time, each frame also shown at the replay time of the page.

    Attributes:
        variants: The image sequences in tab order.
        selected: The position of the variant shown first.
        history: The span of frames offered, ending with the newest stored frame.
        columns: Shows every variant at once in a grid of this many columns, all playing together; 0 shows one
            variant at a time behind tabs.
        follows_replay: Whether the frames follow the replay time of the page; a zoom onto one event plays on its own.
    """

    variants: list[ImageVariant] = Field(min_length=1)
    selected: int = Field(default=0, ge=0)
    history: IsoDuration
    columns: int = Field(default=0, ge=0)
    follows_replay: bool = True

    @model_validator(mode="after")
    def _selected_exists(self) -> Self:
        """Rejects a selected position without variant."""
        if self.selected >= len(self.variants):
            raise ValueError("The selected variant of an image panel must be one of its variants.")
        return self

    def shown_fields(self) -> dict[str, list[str]]:
        """Lists the file fields of the frames as the shown fields of every dataset."""
        shown: dict[str, list[str]] = {}
        for variant in self.variants:
            shown.setdefault(variant.dataset, []).append(variant.field)
        return shown


class FramePayload(BaseModel):
    """One frame of an image sequence payload.

    Attributes:
        time: The UTC time the frame shows.
        url: The path of the frame file relative to the data directory of the site.
    """

    model_config = PAYLOAD

    time: datetime
    url: str


class VariantPayload(BaseModel):
    """One image sequence of an image panel.

    Attributes:
        label: The name on its tab.
        dataset: The dataset ID, which names the directory of its frames.
        frames: The frames in time order.
        freshness: How current the dataset is; empty without frames.
        event: The event a zoom shows, as headline, place, peak time and page; None for other sequences.
    """

    model_config = PAYLOAD

    label: str
    dataset: str
    frames: list[FramePayload]
    freshness: list[DatasetFreshness]
    event: dict[str, str] | None = None


class ImagesPayload(PanelPayload):
    """The JSON document of an image panel.

    Attributes:
        title: The heading.
        variants: The image sequences in tab order.
        selected: The position of the variant shown first.
        columns: The columns of the grid showing every variant at once; 0 for tabs.
        follows_replay: Whether the frames follow the replay time of the page.
    """

    title: str
    variants: list[VariantPayload]
    selected: int
    columns: int
    follows_replay: bool

    def media_files(self) -> list[tuple[str, str]]:
        """Lists the frame files the payload refers to as pairs of dataset ID and file name."""
        return [media_file(frame.url) for variant in self.variants for frame in variant.frames]


def frame_url(dataset: str, file_name: str) -> str:
    """Builds the path of a frame file relative to the data directory of the site."""
    return f"{MEDIA_DIRECTORY}/{dataset}/{file_name}"


def media_file(url: str) -> tuple[str, str]:
    """Splits a path built by frame_url into its dataset ID and file name."""
    dataset, _, file_name = url.removeprefix(f"{MEDIA_DIRECTORY}/").rpartition("/")
    return dataset, file_name


def _variant_payload(variant: ImageVariant, history: timedelta, reader: DashboardReader, context: PanelContext) -> VariantPayload:
    """Builds the payload of one image sequence from its frame documents."""
    frame = reader.recent(variant.dataset, history, (variant.field, *ZOOM_EVENT_FIELDS))
    frames = []
    if not frame.empty and variant.field in frame.columns:
        files = frame[variant.field].dropna()
        frames = [
            FramePayload(time=time, url=frame_url(variant.dataset, str(name)))
            for time, name in zip(index_times(files), files.to_numpy(), strict=True)
        ]
    metadata = reader.metadata(variant.dataset)
    freshness = [] if not frames else [dataset_freshness(variant.dataset, frames[-1].time, metadata, context.feed_intervals.get(variant.dataset))]
    event = None
    if frames:
        # A zoom stores its event with every frame, so the first frame tells it
        first = frame.iloc[0]
        # The reader returns every asked field as a column, so only text values tell that a zoom stored its event
        event = {field.removeprefix("event_"): str(first[field]) for field in ZOOM_EVENT_FIELDS if isinstance(first.get(field), str)} or None
    return VariantPayload(label=variant.label, dataset=variant.dataset, frames=frames, freshness=freshness, event=event)


def build_images(panel: ImagesPanel, reader: DashboardReader, context: PanelContext) -> ImagesPayload:
    """Builds the JSON document of an image panel from the frame documents of its variants.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export, with the feed interval of each dataset.

    Returns:
        The payload.
    """
    return ImagesPayload(
        type=panel.type,
        title=dataset_title(reader.metadata(panel.variants[0].dataset), panel.title),
        variants=[_variant_payload(variant, panel.history, reader, context) for variant in panel.variants],
        selected=panel.selected,
        columns=panel.columns,
        follows_replay=panel.follows_replay,
    )
