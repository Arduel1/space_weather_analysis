import re
from datetime import datetime

import pandas as pd
from pydantic import BaseModel, Field

from space_weather_analysis.clients.donki import EVENT_ID_FIELD, EVENT_TYPE_FIELD, LABEL_FIELD, LINK_FIELD, LINK_SEPARATOR, LINKED_FIELD, VALUE_FIELD
from space_weather_analysis.dashboard.panels.base import CLOSED, PAYLOAD, PanelConfig, PanelContext, PanelPayload, finite_number, text_or_empty
from space_weather_analysis.dashboard.reading import DashboardReader
from space_weather_analysis.metadata.spase.dictionary import spase_definition
from space_weather_analysis.util.time_utils import IsoDuration

# Boundary between two words of a SPASE term written in CamelCase, such as SolarFlare; SPASE (Space Physics Archive Search
# and Extract, https://spase-group.org) is the metadata standard of heliophysics data with a dictionary of defined terms.
# The pattern matches the empty position (?<=[a-z]) right after a lower case letter and (?=[A-Z]) right before an upper
# case letter, so that a substitution inserts text between the words without removing a letter.
CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")


class LaneConfig(BaseModel):
    """One kind of event, drawn on its own lane.

    Attributes:
        event_type: The event type code stored with the events.
        term: The SPASE PhenomenonType term of the kind, which names the lane and gives its definition.
        color: The color of its markers, a CSS color or a theme color token; None for the palette color of its lane.
    """

    model_config = CLOSED

    event_type: str = Field(min_length=1)
    term: str = Field(min_length=1)
    color: str | None = None


class ChainsPanel(PanelConfig):
    """Chains of events that the source links, such as a flare, its coronal mass ejection (CME), the shock at Earth and the storm it caused.

    Attributes:
        dataset: The dataset of the events, one document per event with type, ID and linked IDs.
        history: The span of events shown, ending now.
        lanes: The kinds of event shown, in the order the effects travel from the Sun to the Earth.
    """

    dataset: str = Field(min_length=1)
    history: IsoDuration
    lanes: list[LaneConfig] = Field(min_length=1)


class LanePayload(BaseModel):
    """One kind of event of an event chain view.

    Attributes:
        term: The SPASE term of the kind of event.
        label: The term in words.
        definition: The definition of the SPASE dictionary; None if it is not available.
        color: The color of its markers; None for the palette color of its lane.
    """

    model_config = PAYLOAD

    term: str
    label: str
    definition: str | None
    color: str | None = None


class EventPayload(BaseModel):
    """One event of an event chain view.

    Attributes:
        time: The time of the event.
        lane: The position of its kind in the lanes.
        event_id: The ID of the event at the source.
        label: The headline the source gives, such as the flare class; None without.
        value: The number behind the headline, such as the CME speed; None without.
        link: The page of the event at the source; None without.
    """

    model_config = PAYLOAD

    time: datetime
    lane: int
    event_id: str
    label: str | None
    value: float | None
    link: str | None


class ChainsPayload(PanelPayload):
    """The JSON document of an event chain view.

    Attributes:
        title: The heading.
        lanes: The kinds of event.
        events: The events, oldest first.
        links: The linked pairs of events, as positions in the event list.
        chains: Every group of at least two linked events as positions in the event list, oldest event first; the chain
            with the newest event first.
        unlinked: The number of events linked to no other shown event.
        view: The span searched, ending now, in seconds.
    """

    title: str
    lanes: list[LanePayload]
    events: list[EventPayload]
    links: list[tuple[int, int]]
    chains: list[list[int]]
    unlinked: int
    view: float


def _chains(pairs: list[tuple[int, int]], count: int) -> list[list[int]]:
    """Groups linked events into chains, joining the groups along each pair.

    Args:
        pairs: The linked pairs as positions in the event list, which is sorted by time.
        count: The number of events.

    Returns:
        Each chain as sorted positions, the chain with the newest event first.
    """
    # Union-find: each position points to a parent in its group, a group root to itself
    parent = list(range(count))

    def root(position: int) -> int:
        """Finds the representative of the group of a position, halving the path to it on the way."""
        while parent[position] != position:
            parent[position] = parent[parent[position]]
            position = parent[position]
        return position

    # Linking two events joins their whole groups
    for first, second in pairs:
        parent[root(first)] = root(second)
    # Only linked events form chains; the positions are visited in order, so each chain is sorted
    members: dict[int, list[int]] = {}
    for position in sorted({position for pair in pairs for position in pair}):
        members.setdefault(root(position), []).append(position)
    return sorted(members.values(), key=lambda chain: chain[-1], reverse=True)


def build_chains(panel: ChainsPanel, reader: DashboardReader, context: PanelContext) -> ChainsPayload:
    """Builds the JSON document of an event chain chart from the stored events of the history.

    Args:
        panel: The panel settings.
        reader: The source of documents and metadata.
        context: The context of the export.

    Returns:
        The payload.
    """
    lane_of = {lane.event_type: position for position, lane in enumerate(panel.lanes)}
    fields = (EVENT_TYPE_FIELD, EVENT_ID_FIELD, LINKED_FIELD, LABEL_FIELD, VALUE_FIELD, LINK_FIELD)
    frame = reader.recent(panel.dataset, panel.history, fields)
    # The read ends with the newest event; the view ends now, and events of a kind without lane are left out
    if not frame.empty:
        frame = frame[(frame.index >= pd.Timestamp(context.now) - pd.Timedelta(panel.history)) & frame[EVENT_TYPE_FIELD].isin(lane_of.keys())]
    events: list[EventPayload] = []
    linked_ids: list[list[str]] = []
    ordered = frame.sort_index()
    for time, row in zip(pd.DatetimeIndex(ordered.index), ordered.to_dict("records"), strict=True):
        value = row.get(VALUE_FIELD)
        events.append(
            EventPayload(
                time=time.to_pydatetime(),
                lane=lane_of[row[EVENT_TYPE_FIELD]],
                event_id=str(row[EVENT_ID_FIELD]),
                label=text_or_empty(row.get(LABEL_FIELD)) or None,
                value=finite_number(value),
                link=text_or_empty(row.get(LINK_FIELD)) or None,
            )
        )
        # The source stores the IDs of the linked events in one text, joined by LINK_SEPARATOR
        linked_ids.append(text_or_empty(row.get(LINKED_FIELD)).split(LINK_SEPARATOR))
    position_of = {event.event_id: position for position, event in enumerate(events)}
    # The source lists most links on both events; each pair is kept once, and links to events outside the shown ones are
    # left out
    pairs = sorted(
        {
            (min(position, position_of[other]), max(position, position_of[other]))
            for position, others in enumerate(linked_ids)
            for other in others
            if other in position_of and position_of[other] != position
        }
    )
    lanes = [
        LanePayload(
            term=lane.term, label=CAMEL_CASE_BOUNDARY.sub(" ", lane.term).capitalize(), definition=spase_definition(lane.term), color=lane.color
        )
        for lane in panel.lanes
    ]
    chains = _chains(pairs, len(events))
    return ChainsPayload(
        type=panel.type,
        title=panel.title,
        lanes=lanes,
        events=events,
        links=pairs,
        chains=chains,
        unlinked=len(events) - sum(len(chain) for chain in chains),
        view=panel.history.total_seconds(),
    )
