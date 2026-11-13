from datetime import date

import pandas as pd

from space_weather_analysis.clients.donki import cme_impacts, parse_events, request_spans


def test_requests_never_span_more_than_sixty_days() -> None:
    """A long range is split into consecutive spans that the API accepts, without a day twice or a day missing."""
    spans = list(request_spans(date(2024, 1, 1), date(2024, 4, 30)))

    assert spans == [(date(2024, 1, 1), date(2024, 2, 29)), (date(2024, 3, 1), date(2024, 4, 29)), (date(2024, 4, 30), date(2024, 4, 30))]


def test_events_take_their_headline_and_links_from_their_own_fields() -> None:
    """A CME is placed at its start with the speed of its most accurate analysis; an event without time is left out."""
    answer = [
        {
            "activityID": "2024-05-01T00:00:00-CME-001",
            "startTime": "2024-05-01T00:00Z",
            "cmeAnalyses": [{"isMostAccurate": False, "speed": 500.0}, {"isMostAccurate": True, "speed": 812.0}],
            "linkedEvents": [{"activityID": "2024-04-30T23:40:00-FLR-001"}, {"activityID": "2024-05-03T02:00:00-GST-001"}],
            "link": "https://ccmc.gsfc.nasa.gov/DONKI/view/CME/1/-1",
        },
        {"activityID": "2024-05-02T00:00:00-CME-001", "startTime": None},
    ]

    events = parse_events("CME", answer)

    assert (list(events.index), events.iloc[0].to_dict()) == (
        [pd.Timestamp("2024-05-01T00:00Z")],
        {
            "event_type": "CME",
            "event_id": "2024-05-01T00:00:00-CME-001",
            "linked_ids": "2024-04-30T23:40:00-FLR-001 2024-05-03T02:00:00-GST-001",
            "label": "812 km/s",
            "value": 812.0,
            "link": "https://ccmc.gsfc.nasa.gov/DONKI/view/CME/1/-1",
            "location": None,
            "begin_time": None,
            "end_time": None,
            "cme_latitude": None,
            "cme_longitude": None,
            "cme_half_angle": None,
            "cme_time_21_5": None,
            "impacts": None,
        },
    )


def test_cme_keeps_its_fitted_flight_and_the_arrivals_of_its_newest_enlil_run() -> None:
    """The direction of the most accurate analysis is kept, and per place the newest WSA-Enlil run decides the arrival."""
    analysis = {
        "isMostAccurate": True,
        "latitude": 12.0,
        "longitude": -20.0,
        "halfAngle": 35.0,
        "speed": 900.0,
        "time21_5": "2024-05-01T03:00Z",
        "enlilList": [
            {
                "modelCompletionTime": "2024-05-01T08:00Z",
                "estimatedShockArrivalTime": "2024-05-03T10:00Z",
                "isEarthGB": False,
                "impactList": [{"location": "Mars", "arrivalTime": "2024-05-06T00:00Z", "isGlancingBlow": True}],
            },
            {
                "modelCompletionTime": "2024-05-01T05:00Z",
                "estimatedShockArrivalTime": "2024-05-03T01:00Z",
                "isEarthGB": True,
                "impactList": [{"location": "STEREO A", "arrivalTime": "2024-05-02T20:00Z", "isGlancingBlow": False}],
            },
        ],
    }

    event = parse_events("CME", [{"activityID": "2024-05-01T00:00:00-CME-001", "startTime": "2024-05-01T00:00Z", "cmeAnalyses": [analysis]}]).iloc[0]

    assert (event["cme_latitude"], event["cme_longitude"], event["cme_half_angle"], event["cme_time_21_5"], cme_impacts(analysis)) == (
        12.0,
        -20.0,
        35.0,
        "2024-05-01T03:00Z",
        [
            {"location": "STEREO A", "arrival": "2024-05-02T20:00Z", "glancing": False},
            {"location": "Earth", "arrival": "2024-05-03T10:00Z", "glancing": False},
            {"location": "Mars", "arrival": "2024-05-06T00:00Z", "glancing": True},
        ],
    )


def test_headlines_ignore_values_that_are_no_numbers() -> None:
    """A boolean CME speed gives no headline, and a storm takes its Kp only from the readable entries of its list."""
    cme = {"activityID": "2024-05-01T00:00:00-CME-001", "startTime": "2024-05-01T00:00Z", "cmeAnalyses": [{"isMostAccurate": True, "speed": True}]}
    storm = {"gstID": "2024-05-10T15:00:00-GST-001", "startTime": "2024-05-10T15:00Z", "allKpIndex": ["broken", {"kpIndex": 8.0}, {"kpIndex": "9"}]}

    labels = (parse_events("CME", [cme]).iloc[0]["label"], parse_events("GST", [storm]).iloc[0]["label"])

    assert labels == (None, "Kp 8")
