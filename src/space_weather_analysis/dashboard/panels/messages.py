from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from space_weather_analysis.dashboard.freshness import DatasetFreshness
from space_weather_analysis.dashboard.labels import dataset_title
from space_weather_analysis.dashboard.panels.base import PAYLOAD, PanelConfig, PanelContext, PanelPayload
from space_weather_analysis.dashboard.panels.series import index_times, read_freshness
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.util.time_utils import IsoDuration


class MessagesPanel(PanelConfig):
    """The newest text messages a provider publishes, such as alerts and warnings, in its own words.

    Attributes:
        dataset: The dataset ID of the messages, which must belong to a feed.
        text_field: The field holding the message text.
        title_field: The field holding a short name of the message; empty for none.
        history: The span searched for messages, ending with the newest stored message.
        count: The number of messages shown.
    """

    dataset: str = Field(min_length=1)
    text_field: str = Field(min_length=1)
    title_field: str = ""
    history: IsoDuration = timedelta(days=3)
    count: int = Field(default=10, gt=0)

    def shown_fields(self) -> dict[str, list[str]]:
        """Lists the text and title field as the shown fields of the dataset."""
        return {self.dataset: [field for field in (self.title_field, self.text_field) if field]}


class MessagePayload(BaseModel):
    """One message of a messages payload.

    Attributes:
        time: The UTC time the message was issued.
        title: The short name of the message; empty without title field.
        text: The message text as the provider wrote it.
    """

    model_config = PAYLOAD

    time: datetime
    title: str
    text: str


class MessagesPayload(PanelPayload):
    """The JSON document of a messages panel.

    Attributes:
        title: The heading.
        messages: The newest messages first.
        freshness: How current the dataset is; empty without messages.
    """

    title: str
    messages: list[MessagePayload]
    freshness: list[DatasetFreshness]


def build_messages(panel: MessagesPanel, reader: DashboardReader, context: PanelContext) -> MessagesPayload:
    """Builds the JSON document of the newest messages of a dataset.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export, with the feed interval of each dataset.

    Returns:
        The payload.
    """
    fields = tuple(field for field in (panel.title_field, panel.text_field) if field)
    frame = reader.recent(panel.dataset, panel.history, fields)
    metadata = reader.metadata(panel.dataset)
    messages = []
    if not frame.empty and panel.text_field in frame.columns:
        newest = frame.sort_index(ascending=False).head(panel.count)
        for time, row in zip(index_times(newest), newest.to_dict("records"), strict=True):
            text = row.get(panel.text_field)
            # A message without text has nothing to show
            if isinstance(text, str):
                title = row.get(panel.title_field) if panel.title_field else ""
                # Some providers end lines Windows style with CR LF; the payload holds plain line breaks
                messages.append(MessagePayload(time=time, title=str(title or ""), text=text.replace("\r\n", "\n")))
    freshness = read_freshness([(panel.dataset, frame)], lambda _dataset: metadata, context)
    return MessagesPayload(type=panel.type, title=dataset_title(metadata, panel.title), messages=messages, freshness=freshness)
