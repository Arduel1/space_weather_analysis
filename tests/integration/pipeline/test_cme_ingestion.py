import pathlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo.database import Database

from pipeline_service.pipelines.local import LocalPipeline

StoredData = Callable[[str, datetime], dict[str, Any]]
DataDirWithExtraRow = Callable[[str, str], pathlib.Path]
StoredTimestampsAndMetadata = Callable[[str], list[tuple[datetime, dict[str, Any]]]]
LocalPipelineFor = Callable[[pathlib.Path], LocalPipeline]
WriteDatasetInput = Callable[[pathlib.Path, str], None]

CME_COLLECTION = "cme_catalog"
SOURCE_METADATA = {"source": "LOCAL/SOHO/LASCO_CDAW_V2.0"}

# Events in data/synthetic_cme_catalog.txt, invented values in the SOHO/LASCO CME catalog layout.
REGULAR_EVENT_TIME = datetime(2003, 5, 10, 4, 12, 30, tzinfo=UTC)
HALO_EVENT_TIME = datetime(2003, 5, 11, 17, 45, 6, tzinfo=UTC)
PLACEHOLDER_EVENT_TIME = datetime(2003, 5, 12, 9, 3, 51, tzinfo=UTC)
EVENT_TIMES = [REGULAR_EVENT_TIME, HALO_EVENT_TIME, PLACEHOLDER_EVENT_TIME]

# A valid event row appended to the synthetic file.
EXTRA_EVENT_ROW = "2003/05/13  11:30:00    120     45    300     290    310    320      1.5    1.0e+15    4.0e+29    118   Poor Event"


@pytest.mark.integration
def test_cme_ingestion_stores_one_document_per_event(
    db: Database, cme_dir: pathlib.Path, local_pipeline: LocalPipelineFor, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """Each event row yields one document at its UTC date and time with the catalog version as source."""
    local_pipeline(cme_dir).run([CME_COLLECTION])

    assert stored_timestamps_and_metadata(CME_COLLECTION) == [(time, SOURCE_METADATA) for time in EVENT_TIMES]


@pytest.mark.integration
def test_cme_documents_hold_values_with_their_types_and_omit_missing_ones(
    db: Database, cme_dir: pathlib.Path, local_pipeline: LocalPipelineFor, stored_data: StoredData
) -> None:
    """Numbers, flags and remarks are stored with their types; placeholders, the halo angle and unset flags leave their fields out."""
    local_pipeline(cme_dir).run([CME_COLLECTION])

    stored = {time: stored_data(CME_COLLECTION, time) for time in EVENT_TIMES}
    assert stored == {
        REGULAR_EVENT_TIME: pytest.approx(
            {
                "central_pa": 211,
                "width": 34,
                "linear_speed": 412,
                "speed_initial": 430,
                "speed_final": 395,
                "speed_at_20r": 0,
                "accel": -9.8,
                "mass": 2.4e15,
                "kinetic_energy": 6.2e29,
                "mpa": 207,
                "is_halo": False,
                "accel_unreliable": True,
                "remarks": "Poor Event;  Only C3; Partial Halo",
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
                "is_halo": True,
                "mass_uncertain": True,
                "kinetic_energy_uncertain": True,
            }
        ),
        PLACEHOLDER_EVENT_TIME: pytest.approx(
            {"central_pa": 58, "width": 21, "mass": 1.7e14, "mpa": 61, "is_halo": False, "mass_uncertain": True, "remarks": "Very Poor Event"}
        ),
    }
    assert {type(data["is_halo"]) for data in stored.values()} == {bool}


@pytest.mark.integration
def test_repeated_cme_ingestion_replaces_stored_documents(
    db: Database,
    tmp_path: pathlib.Path,
    data_dir_with_extra_row: DataDirWithExtraRow,
    write_dataset_input: WriteDatasetInput,
    local_pipeline: LocalPipelineFor,
    stored_timestamps_and_metadata: StoredTimestampsAndMetadata,
) -> None:
    """A second run stores exactly the events of the current catalog, so an event it no longer holds is removed and none is duplicated."""
    local_pipeline(data_dir_with_extra_row(CME_COLLECTION, EXTRA_EVENT_ROW)).run([CME_COLLECTION])
    write_dataset_input(tmp_path, CME_COLLECTION)

    local_pipeline(tmp_path).run([CME_COLLECTION])

    assert [time for time, _ in stored_timestamps_and_metadata(CME_COLLECTION)] == EVENT_TIMES
