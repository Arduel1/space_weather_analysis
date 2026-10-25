import pathlib
from datetime import datetime
from typing import Any, Callable, Dict, List, Tuple

import pytest
from pymongo.database import Database

from pipeline_service.pipelines.local import LocalPipeline

StoredData = Callable[[str, datetime], Dict[str, Any]]
DataDirWithExtraRow = Callable[[str, str], pathlib.Path]
StoredTimestampsAndMetadata = Callable[[str], List[Tuple[datetime, Dict[str, Any]]]]

CME_COLLECTION = "cme_catalog"
SOURCE_METADATA = {"source": "SOHO/LASCO_CDAW_V2.0"}

# Events in data/synthetic_cme_catalog.txt, invented values in the SOHO/LASCO CME catalog layout.
REGULAR_EVENT_TIME = datetime(2003, 5, 10, 4, 12, 30)
HALO_EVENT_TIME = datetime(2003, 5, 11, 17, 45, 6)
PLACEHOLDER_EVENT_TIME = datetime(2003, 5, 12, 9, 3, 51)
EVENT_TIMES = [REGULAR_EVENT_TIME, HALO_EVENT_TIME, PLACEHOLDER_EVENT_TIME]

NUMERIC_FIELDS = ["width", "linear_speed", "speed_initial", "speed_final", "speed_at_20r", "accel", "mass", "kinetic_energy", "mpa"]
CENTRAL_PA_FIELDS = ["central_pa"]
# Set by a trailing '*': acceleration from only three measurements, mass or kinetic energy with large uncertainties.
MARKER_FIELDS = ["accel_unreliable", "mass_uncertain", "kinetic_energy_uncertain"]
# Fields given as dashes (unknown value) in the placeholder event, covering every dash length.
PLACEHOLDER_FIELDS = ["linear_speed", "speed_initial", "speed_final", "speed_at_20r", "accel", "kinetic_energy"]

# Rows appended to the synthetic file for the invalid input cases.
NON_NUMERIC_WIDTH_ROW = "2003/05/13  11:30:00    120    abc    300     290    310    320      1.5    1.0e+15    4.0e+29    118   Poor Event"
NON_NUMERIC_WIDTH_EVENT_TIME = datetime(2003, 5, 13, 11, 30, 0)
INVALID_DATE_ROW = "2003/13/01  06:00:00    120     45    300     290    310    320      1.5    1.0e+15    4.0e+29    118   Poor Event"


def _fields_of_events(stored_data: StoredData, times: List[datetime], fields: List[str]) -> Dict[datetime, Dict[str, Any]]:
    """Returns the given fields present in the stored document of each event, keyed by timestamp."""
    return {time: {field: value for field, value in stored_data(CME_COLLECTION, time).items() if field in fields} for time in times}


@pytest.mark.integration
def test_cme_ingestion_stores_one_document_per_event_row_at_its_time(
    db: Database, cme_dir: pathlib.Path, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """Header lines are skipped; each event row yields one document at its date and time (naive UTC) with the CDAW source."""
    LocalPipeline(db, cme_dir).run([CME_COLLECTION])

    # pymongo returns stored datetimes as naive UTC, so the expected times carry no time zone.
    assert stored_timestamps_and_metadata(CME_COLLECTION) == [(time, SOURCE_METADATA) for time in EVENT_TIMES]


@pytest.mark.integration
def test_cme_numeric_columns_stored_as_numbers(db: Database, cme_dir: pathlib.Path, stored_data: StoredData) -> None:
    """Numeric columns are stored as numbers, including a speed of 0 and values with a '*' marker."""
    LocalPipeline(db, cme_dir).run([CME_COLLECTION])

    assert _fields_of_events(stored_data, [REGULAR_EVENT_TIME, HALO_EVENT_TIME], NUMERIC_FIELDS) == {
        REGULAR_EVENT_TIME: pytest.approx(
            {
                "width": 34,
                "linear_speed": 412,
                "speed_initial": 430,
                "speed_final": 395,
                "speed_at_20r": 0,
                "accel": -9.8,
                "mass": 2.4e15,
                "kinetic_energy": 6.2e29,
                "mpa": 207,
            }
        ),
        HALO_EVENT_TIME: pytest.approx(
            {
                "width": 360,
                "linear_speed": 905,
                "speed_initial": 880,
                "speed_final": 931,
                "speed_at_20r": 960,
                "accel": 12.7,
                "mass": 3.3e15,
                "kinetic_energy": 1.1e31,
                "mpa": 145,
            }
        ),
    }


@pytest.mark.integration
def test_cme_is_halo_set_from_central_position_angle(db: Database, cme_dir: pathlib.Path, stored_data: StoredData) -> None:
    """is_halo is true for central position angle 'Halo' and false for a numeric angle, despite 'Partial Halo' remarks."""
    LocalPipeline(db, cme_dir).run([CME_COLLECTION])

    stored = {time: stored_data(CME_COLLECTION, time)["is_halo"] for time in (HALO_EVENT_TIME, REGULAR_EVENT_TIME)}
    assert stored == {HALO_EVENT_TIME: True, REGULAR_EVENT_TIME: False}
    assert all(isinstance(value, bool) for value in stored.values())


@pytest.mark.integration
@pytest.mark.xfail(strict=True, reason="central_pa is stored as the raw catalog string, 'Halo' for a halo event")
def test_cme_central_pa_numeric_for_non_halo_and_absent_for_halo(db: Database, cme_dir: pathlib.Path, stored_data: StoredData) -> None:
    """central_pa is a number for a non-halo event and absent for a halo event."""
    LocalPipeline(db, cme_dir).run([CME_COLLECTION])

    stored = _fields_of_events(stored_data, [REGULAR_EVENT_TIME, HALO_EVENT_TIME], CENTRAL_PA_FIELDS)
    assert stored == {REGULAR_EVENT_TIME: {"central_pa": 211}, HALO_EVENT_TIME: {}}
    assert not isinstance(stored[REGULAR_EVENT_TIME]["central_pa"], str)


@pytest.mark.integration
@pytest.mark.xfail(strict=True, reason="placeholder values are stored as NaN")
def test_cme_placeholder_values_absent_from_document(db: Database, cme_dir: pathlib.Path, stored_data: StoredData) -> None:
    """Fields with a dash placeholder are omitted while the event is stored."""
    LocalPipeline(db, cme_dir).run([CME_COLLECTION])

    data = stored_data(CME_COLLECTION, PLACEHOLDER_EVENT_TIME)
    assert {field: data[field] for field in PLACEHOLDER_FIELDS if field in data} == {}


@pytest.mark.integration
@pytest.mark.xfail(
    strict=True,
    reason="the '*' marker is cut off the value and no marker flag field is stored",
)
def test_cme_marker_flags_set_only_for_marked_values(db: Database, cme_dir: pathlib.Path, stored_data: StoredData) -> None:
    """A '*' sets the flag of its value (acceleration, mass, kinetic energy) to true, only on marked events."""
    LocalPipeline(db, cme_dir).run([CME_COLLECTION])

    stored = _fields_of_events(stored_data, EVENT_TIMES, MARKER_FIELDS)
    assert stored == {
        REGULAR_EVENT_TIME: {"accel_unreliable": True},
        HALO_EVENT_TIME: {"mass_uncertain": True, "kinetic_energy_uncertain": True},
        PLACEHOLDER_EVENT_TIME: {"mass_uncertain": True},
    }
    assert all(value is True for flags in stored.values() for value in flags.values())


@pytest.mark.integration
@pytest.mark.xfail(strict=True, reason="whitespace runs in the remarks are collapsed into one space")
def test_cme_remarks_stored_exactly_as_given(db: Database, cme_dir: pathlib.Path, stored_data: StoredData) -> None:
    """Remarks are stored verbatim, including semicolons and repeated spaces."""
    LocalPipeline(db, cme_dir).run([CME_COLLECTION])

    data = stored_data(CME_COLLECTION, REGULAR_EVENT_TIME)
    assert data["remarks"] == "Poor Event;  Only C3; Partial Halo"


@pytest.mark.integration
@pytest.mark.xfail(strict=True, reason="an event without remarks stores remarks as the empty string")
def test_cme_remarks_absent_when_event_has_none(db: Database, cme_dir: pathlib.Path, stored_data: StoredData) -> None:
    """An event row without remarks is stored without a remarks field."""
    LocalPipeline(db, cme_dir).run([CME_COLLECTION])

    data = stored_data(CME_COLLECTION, HALO_EVENT_TIME)
    assert "remarks" not in data


@pytest.mark.integration
@pytest.mark.xfail(strict=True, reason="the non-numeric width is stored as NaN")
def test_cme_non_numeric_value_affects_only_its_own_field(
    db: Database, data_dir_with_extra_row: DataDirWithExtraRow, stored_data: StoredData
) -> None:
    """A non-numeric width omits only that field; the other numeric values are stored."""
    data_dir = data_dir_with_extra_row(CME_COLLECTION, NON_NUMERIC_WIDTH_ROW)

    LocalPipeline(db, data_dir).run([CME_COLLECTION])

    assert _fields_of_events(stored_data, [NON_NUMERIC_WIDTH_EVENT_TIME], NUMERIC_FIELDS) == {
        NON_NUMERIC_WIDTH_EVENT_TIME: pytest.approx(
            {
                "linear_speed": 300,
                "speed_initial": 290,
                "speed_final": 310,
                "speed_at_20r": 320,
                "accel": 1.5,
                "mass": 1.0e15,
                "kinetic_energy": 4.0e29,
                "mpa": 118,
            }
        )
    }


@pytest.mark.integration
def test_cme_event_row_skipped_when_date_is_invalid(
    db: Database, data_dir_with_extra_row: DataDirWithExtraRow, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """An event row with an invalid calendar date is skipped; the other rows are stored."""
    data_dir = data_dir_with_extra_row(CME_COLLECTION, INVALID_DATE_ROW)

    LocalPipeline(db, data_dir).run([CME_COLLECTION])

    assert [time for time, _ in stored_timestamps_and_metadata(CME_COLLECTION)] == EVENT_TIMES


@pytest.mark.integration
def test_cme_ingestion_creates_timeseries_collection(db: Database, cme_dir: pathlib.Path) -> None:
    """The collection is a time series with time field 'timestamp' and meta field 'metadata'."""
    LocalPipeline(db, cme_dir).run([CME_COLLECTION])

    infos = list(db.list_collections(filter={"name": CME_COLLECTION}))
    assert len(infos) == 1
    assert infos[0]["type"] == "timeseries"
    assert infos[0]["options"]["timeseries"]["timeField"] == "timestamp"
    assert infos[0]["options"]["timeseries"]["metaField"] == "metadata"


@pytest.mark.integration
@pytest.mark.xfail(strict=True, reason="every run appends all documents again, so a second run duplicates them")
def test_repeated_cme_ingestion_does_not_duplicate_documents(db: Database, cme_dir: pathlib.Path) -> None:
    """Ingesting the unchanged catalog twice keeps one document per event row."""
    LocalPipeline(db, cme_dir).run([CME_COLLECTION])
    LocalPipeline(db, cme_dir).run([CME_COLLECTION])

    assert db[CME_COLLECTION].count_documents({}) == len(EVENT_TIMES)
