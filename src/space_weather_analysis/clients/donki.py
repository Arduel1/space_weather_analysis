import json
import logging
import math
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any

import pandas as pd
import requests

from space_weather_analysis.clients.download import download_json
from space_weather_analysis.util.error_handling import DataProcessingError

logger = logging.getLogger(__name__)

# DONKI (Space Weather Database Of Notifications, Knowledge, Information, https://ccmc.gsfc.nasa.gov/tools/DONKI/)
# of the Community Coordinated Modeling Center lists space weather events with the links forecasters set between them
DONKI_API_URL = "https://ccmc.gsfc.nasa.gov/DONKI-API/get/{event_type}?startDate={start}&endDate={end}"
DONKI_PAGE_URL = "https://ccmc.gsfc.nasa.gov/tools/DONKI/"


class DonkiEventType(StrEnum):
    """The event types fetched, by the code DONKI names them with and stores in EVENT_TYPE_FIELD."""

    FLARE = "FLR"
    CME = "CME"
    INTERPLANETARY_SHOCK = "IPS"
    SOLAR_ENERGETIC_PARTICLES = "SEP"
    HIGH_SPEED_STREAM = "HSS"
    GEOMAGNETIC_STORM = "GST"


# The codes of the event types fetched, in the order of the requests, as plain text for the URLs and stored documents
DONKI_EVENT_TYPES = tuple(event_type.value for event_type in DonkiEventType)

# Longest span of one request; the API refuses spans over 60 days, both dates included
DONKI_MAX_SPAN = timedelta(days=59)

# Connect and read timeout in seconds
DONKI_TIMEOUT = (10.0, 60.0)

# Fields naming an event, of which each event type uses one
ID_FIELDS = ("activityID", "flrID", "gstID", "hssID", "sepID")
# Fields holding the time of an event, of which each event type uses one; a flare is placed at its peak
TIME_FIELDS = ("peakTime", "startTime", "eventTime")

# Stored fields of an event document
EVENT_TYPE_FIELD = "event_type"
EVENT_ID_FIELD = "event_id"
LINKED_FIELD = "linked_ids"
LABEL_FIELD = "label"
VALUE_FIELD = "value"
LINK_FIELD = "link"
# The place on the Sun such as S08E35, and the begin and end of a flare as ISO 8601 text
LOCATION_FIELD = "location"
BEGIN_FIELD = "begin_time"
END_FIELD = "end_time"
# The flight of a CME as the most accurate analysis of DONKI fits it: direction in heliographic latitude and longitude
# from the Sun-Earth line (degrees, west positive), half the opening angle of its cone (degrees) and the time its front
# passed 21.5 solar radii as ISO 8601 text, from where models such as WSA-Enlil carry it outwards. WSA-Enlil couples the
# Wang-Sheeley-Arge model of the solar corona with the Enlil model of the solar wind between the Sun and the planets
CME_LATITUDE_FIELD = "cme_latitude"
CME_LONGITUDE_FIELD = "cme_longitude"
CME_HALF_ANGLE_FIELD = "cme_half_angle"
CME_TIME_21_5_FIELD = "cme_time_21_5"
# Where the WSA-Enlil runs of a CME expect it to arrive, as JSON text: a list of objects with the place as DONKI names
# it (Earth, STEREO A, Mars ...), the arrival as ISO 8601 text and whether it only grazes the place
IMPACTS_FIELD = "impacts"
EVENT_FIELDS = (
    EVENT_TYPE_FIELD,
    EVENT_ID_FIELD,
    LINKED_FIELD,
    LABEL_FIELD,
    VALUE_FIELD,
    LINK_FIELD,
    LOCATION_FIELD,
    BEGIN_FIELD,
    END_FIELD,
    CME_LATITUDE_FIELD,
    CME_LONGITUDE_FIELD,
    CME_HALF_ANGLE_FIELD,
    CME_TIME_21_5_FIELD,
    IMPACTS_FIELD,
)
# DONKI names Earth in the arrival fields of a WSA-Enlil run instead of its list of impacts
EARTH = "Earth"

# Linked event IDs are stored as one text, separated by this
LINK_SEPARATOR = " "


def request_spans(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Splits a range of days into the spans of single requests, both days of a span included.

    Args:
        start: The first day.
        end: The last day.

    Yields:
        The first and last day of each request, oldest first.
    """
    span_start = start
    while span_start <= end:
        span_end = min(span_start + DONKI_MAX_SPAN, end)
        yield span_start, span_end
        span_start = span_end + timedelta(days=1)


def _first(event: dict[str, Any], fields: tuple[str, ...]) -> Any:
    """Returns the value of the first of the fields an event holds; None if it holds none."""
    return next((event[name] for name in fields if event.get(name)), None)


def _best_analysis(event: dict[str, Any]) -> dict[str, Any] | None:
    """Returns the analysis of a CME that DONKI marks as most accurate, else its first; None without analysis."""
    analyses = [analysis for analysis in event.get("cmeAnalyses") or [] if isinstance(analysis, dict)]
    return next((analysis for analysis in analyses if analysis.get("isMostAccurate")), analyses[0] if analyses else None)


def _number(value: Any) -> float | None:
    """Returns a finite number as float; None for anything else, booleans, NaN and infinity included."""
    # bool is a subclass of int but no measurement
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) else None


def cme_impacts(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Collects where the WSA-Enlil runs of a CME analysis expect the CME to arrive, the newest run deciding per place.

    Args:
        analysis: One analysis of a DONKI CME with its list of WSA-Enlil runs.

    Returns:
        One object per place with `location`, `arrival` (ISO 8601 text) and `glancing`, sorted by arrival.
    """
    runs = [run for run in analysis.get("enlilList") or [] if isinstance(run, dict)]
    impacts: dict[str, dict[str, Any]] = {}
    # Oldest run first, so that a newer run overwrites the expected arrival of a place
    for run in sorted(runs, key=lambda run: str(run.get("modelCompletionTime") or "")):
        # The arrival at Earth has fields of its own; the other places are in the impact list
        if run.get("estimatedShockArrivalTime"):
            impacts[EARTH] = {"location": EARTH, "arrival": run["estimatedShockArrivalTime"], "glancing": bool(run.get("isEarthGB"))}
        for impact in run.get("impactList") or []:
            if isinstance(impact, dict) and impact.get("location") and impact.get("arrivalTime"):
                location = impact["location"]
                impacts[location] = {"location": location, "arrival": impact["arrivalTime"], "glancing": bool(impact.get("isGlancingBlow"))}
    return sorted(impacts.values(), key=lambda impact: str(impact["arrival"]))


def _flight(event_type: str, event: dict[str, Any]) -> dict[str, Any]:
    """Takes the fitted flight and the expected arrivals of a CME from its most accurate analysis; nothing for other events."""
    analysis = _best_analysis(event) if event_type == DonkiEventType.CME else None
    if analysis is None:
        return {}
    impacts = cme_impacts(analysis)
    return {
        CME_LATITUDE_FIELD: _number(analysis.get("latitude")),
        CME_LONGITUDE_FIELD: _number(analysis.get("longitude")),
        CME_HALF_ANGLE_FIELD: _number(analysis.get("halfAngle")),
        CME_TIME_21_5_FIELD: analysis.get("time21_5") or None,
        IMPACTS_FIELD: json.dumps(impacts) if impacts else None,
    }


def _summary(event_type: str, event: dict[str, Any]) -> tuple[str | None, float | None]:
    """Takes the headline of an event from its own fields: flare class, CME speed, storm Kp or shock location.

    Returns:
        The text and the number shown with the event; None where the event type has none or the event lacks it.
    """
    if event_type == DonkiEventType.FLARE:
        return event.get("classType"), None
    if event_type == DonkiEventType.CME:
        best = _best_analysis(event)
        speed = _number(best.get("speed")) if best else None
        return (f"{speed:.0f} km/s", speed) if speed is not None else (None, None)
    if event_type == DonkiEventType.GEOMAGNETIC_STORM:
        kp_entries = [entry for entry in event.get("allKpIndex") or [] if isinstance(entry, dict)]
        kp_numbers = [_number(entry.get("kpIndex")) for entry in kp_entries]
        kp_values = [kp for kp in kp_numbers if kp is not None]
        if not kp_values:
            return None, None
        highest_kp = max(kp_values)
        return f"Kp {highest_kp:g}", float(highest_kp)
    if event_type == DonkiEventType.INTERPLANETARY_SHOCK:
        return event.get("location"), None
    return None, None


def parse_events(event_type: str, events: Any) -> pd.DataFrame:
    """Reads the events of one type from a DONKI answer into one row per event.

    An event without ID or valid time is left out.

    Args:
        event_type: The DONKI code of the event type.
        events: The parsed JSON answer, a list of events.

    Returns:
        The events with a UTC DatetimeIndex and the columns of EVENT_FIELDS; empty without events.

    Raises:
        DataProcessingError: If the answer is no list.
    """
    if not isinstance(events, list):
        raise DataProcessingError(f"DONKI answer for {event_type} is no list of events.")
    rows = []
    times = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = _first(event, ID_FIELDS)
        time = pd.to_datetime(_first(event, TIME_FIELDS), utc=True, errors="coerce")
        if not event_id or pd.isna(time):
            continue
        # The IDs of the events forecasters linked to this one, such as the CME of a flare
        linked = [link["activityID"] for link in event.get("linkedEvents") or [] if isinstance(link, dict) and link.get("activityID")]
        label, value = _summary(event_type, event)
        rows.append(
            {
                EVENT_TYPE_FIELD: event_type,
                EVENT_ID_FIELD: str(event_id),
                LINKED_FIELD: LINK_SEPARATOR.join(linked),
                LABEL_FIELD: label,
                VALUE_FIELD: value,
                LINK_FIELD: event.get("link"),
                LOCATION_FIELD: event.get("sourceLocation") or None,
                BEGIN_FIELD: event.get("beginTime") or None,
                END_FIELD: event.get("endTime") or None,
                **_flight(event_type, event),
            }
        )
        times.append(time)
    return pd.DataFrame(rows, columns=list(EVENT_FIELDS), index=pd.DatetimeIndex(times, tz=UTC))


def fetch_events(session: requests.Session, start: datetime, stop: datetime) -> pd.DataFrame:
    """Fetches the events of every type of DONKI_EVENT_TYPES whose day lies in a range.

    Args:
        session: The HTTP session for the requests.
        start: The start of the range; its day is the first day requested.
        stop: The end of the range; its day is the last day requested.

    Returns:
        The events of all types, sorted by time.

    Raises:
        DataRetrievalError: If a request failed.
        SourceUnreachableError: If DONKI sent no answer.
        DataProcessingError: If an answer is no list of events.
    """
    rows: list[dict[str, Any]] = []
    times: list[pd.Timestamp] = []
    for event_type in DONKI_EVENT_TYPES:
        # The API takes whole days and refuses long spans, so a long range takes several requests
        for first_day, last_day in request_spans(start.astimezone(UTC).date(), stop.astimezone(UTC).date()):
            url = DONKI_API_URL.format(event_type=event_type, start=first_day.isoformat(), end=last_day.isoformat())
            frame = parse_events(event_type, download_json(session, url, DONKI_TIMEOUT, f"DONKI {event_type} events"))
            # Rows instead of frames are joined, as frames with columns of only missing values do not concatenate cleanly
            rows.extend({str(name): value for name, value in record.items()} for record in frame.to_dict("records"))
            times.extend(frame.index)
    events = pd.DataFrame(rows, columns=list(EVENT_FIELDS), index=pd.DatetimeIndex(times, tz=UTC))
    logger.info("Fetched %d DONKI events from %s to %s", len(events), start, stop)
    return events.sort_index()
