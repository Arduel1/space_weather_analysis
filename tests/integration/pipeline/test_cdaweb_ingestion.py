from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import numpy as np
import pytest
import xarray as xr
from pymongo.database import Database

from pipeline_service.chunk_status.repository import MongoChunkStatusRepository
from pipeline_service.pipelines.cdaweb import CdawebPipeline
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import CatalogEntryDict
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.coverage.repository import MongoCoverageRepository
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSourceKind
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.timeseries.models import TimeBucket
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository

StoredData = Callable[[str, datetime], dict[str, Any]]
StoredTimestampsAndMetadata = Callable[[str], list[tuple[datetime, dict[str, Any]]]]

DATASET_ID = "SYNTH_MAG_H0"
GROUP = "SYNTH"
SOURCE_METADATA = {"source": f"CDAWEB/{GROUP}"}

DAY_ONE = datetime(2024, 1, 1, tzinfo=UTC)
DAY_TWO = DAY_ONE + timedelta(days=1)
DAY_THREE = DAY_ONE + timedelta(days=2)
RECORD_INTERVAL = timedelta(hours=6)
RECORDS_PER_DAY = 4

# Invented values of the four daily records: record 1 holds the float fill, record 2 the integer fill,
# record 3 values outside VALIDMIN/VALIDMAX in flux and in component 1 of B.
FLOAT_FILL = -1e31
INT_FILL = -2147483648
FLUX_VALUES = [1.5, FLOAT_FILL, 3.5, 250.0]
QUALITY_VALUES = [0, 1, INT_FILL, 3]
# Component 2 of record 3 lies outside the range of component 0 but inside its own.
B_VALUES = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0], [9.0, 25.0, -29.0]]
B_VALID_MIN = [-10.0, -20.0, -30.0]
B_VALID_MAX = [10.0, 20.0, 30.0]
# Twelve components, above the column limit of 10.
SPECTRUM_COMPONENTS = 12
# The slow variable has one record per day at 15:00, between the records of the main time axis.
SLOW_OFFSET = timedelta(hours=15)

ALL_VARIABLES = ["flux", "quality", "B", "spectrum", "image", "unplaced", "slow"]


def _record_time(day: datetime, record_number: int) -> datetime:
    """Returns the epoch of one daily record."""
    return day + record_number * RECORD_INTERVAL


def _days_touching(start: datetime, stop: datetime) -> list[datetime]:
    """Returns the days from the day of start to the day of stop, as CDAS delivers whole daily records here."""
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    days = []
    while day <= stop:
        days.append(day)
        day += timedelta(days=1)
    return days


def _synthetic_response(days: list[datetime], variables: list[str], bin_location: float | None) -> xr.Dataset:
    """Builds a dataset shaped like a cdasws XARRAY response with the requested variables of the days."""
    record_times = np.array(
        [(day + number * RECORD_INTERVAL).replace(tzinfo=None) for day in days for number in range(RECORDS_PER_DAY)], dtype="datetime64[ns]"
    )
    slow_times = np.array([(day + SLOW_OFFSET).replace(tzinfo=None) for day in days], dtype="datetime64[ns]")
    record_count = len(record_times)
    epoch_attributes: dict[str, Any] = {"FIELDNAM": "Epoch", "VAR_TYPE": "support_data", "UNITS": "Datetime (UTC)"}
    if bin_location is not None:
        epoch_attributes["BIN_LOCATION"] = np.float64(bin_location)
    data_attributes = {"DEPEND_0": "epoch", "VAR_TYPE": "data"}
    candidates = {
        "flux": (
            ("epoch",),
            np.tile(FLUX_VALUES, len(days)),
            {
                **data_attributes,
                "CATDESC": "Synthetic flux",
                "FILLVAL": np.float64(FLOAT_FILL),
                "UNITS": "nT",
                "VALIDMIN": np.float64(0.0),
                "VALIDMAX": np.float64(100.0),
            },
        ),
        "quality": (
            ("epoch",),
            np.tile(np.array(QUALITY_VALUES, dtype=np.int32), len(days)),
            {**data_attributes, "CATDESC": "Synthetic quality flag", "FILLVAL": np.int32(INT_FILL), "UNITS": " "},
        ),
        "B": (
            ("epoch", "cartesian"),
            np.tile(np.array(B_VALUES), (len(days), 1)),
            {
                **data_attributes,
                "CATDESC": "Synthetic magnetic field vector",
                "FILLVAL": np.float64(FLOAT_FILL),
                "UNITS": "nT",
                "VALIDMIN": np.array(B_VALID_MIN),
                "VALIDMAX": np.array(B_VALID_MAX),
                "LABL_PTR_1": "metavar0",
                "DEPEND_1": "cartesian",
            },
        ),
        "spectrum": (("epoch", "energy"), np.ones((record_count, SPECTRUM_COMPONENTS)), {**data_attributes, "CATDESC": "Synthetic spectrum"}),
        "image": (("epoch", "x", "y"), np.ones((record_count, 2, 2)), {**data_attributes, "CATDESC": "Synthetic image"}),
        # The data lie on a record dimension without time coordinate, while DEPEND_0 names another variable
        "unplaced": (("record1",), np.ones(record_count), {**data_attributes, "DEPEND_0": "Epoch2", "CATDESC": "Synthetic misplaced variable"}),
        "slow": (
            ("epoch_slow",),
            np.arange(len(days), dtype=np.float64),
            {**data_attributes, "DEPEND_0": "epoch_slow", "CATDESC": "Synthetic daily value"},
        ),
    }
    data_variables = {name: candidates[name] for name in variables if name in candidates}
    coordinates = {
        "epoch": ("epoch", record_times, epoch_attributes),
        "epoch_slow": ("epoch_slow", slow_times, {**epoch_attributes, "FIELDNAM": "Epoch of the daily value"}),
        "cartesian": ("cartesian", ["x_component", "y_component", "z_component"]),
        "metavar0": ("cartesian", ["Bx", "By", "Bz"]),
    }
    return xr.Dataset(data_variables, coords=coordinates)


class FakeCdasWs:
    """CDAS web service client answering with synthetic responses, recording its calls."""

    def __init__(self, status_code: int = 200, bin_location: float | None = None, slow_until: datetime | None = None) -> None:
        """Starts without calls; every data request is answered with the given HTTP status, with the slow variable only before slow_until."""
        self.status_code = status_code
        self.bin_location = bin_location
        self.slow_until = slow_until
        self.data_calls: list[tuple[str, list[str], datetime, datetime]] = []
        self.variable_name_calls = 0

    def get_variable_names(self, dataset_id: str) -> list[str]:
        """Lists every synthetic variable, as CDAS lists the variables of a dataset."""
        self.variable_name_calls += 1
        return list(ALL_VARIABLES)

    def get_data(self, dataset_id: str, variables: list[str], start: datetime, stop: datetime, **keywords: Any) -> tuple[dict[str, Any], Any]:
        """Answers like cdasws: the status and, for HTTP 200, the requested variables of the days touching the range."""
        self.data_calls.append((dataset_id, variables, start, stop))
        status = {"http": {"status_code": self.status_code}, "cdas": {"status": [], "message": [], "warning": [], "error": []}}
        if self.status_code != 200:
            return status, None
        if self.slow_until is not None and start >= self.slow_until:
            variables = [name for name in variables if name != "slow"]
        return status, _synthetic_response(_days_touching(start, stop), variables, self.bin_location)


def _ingest(db: Database, cdas: FakeCdasWs, start: datetime = DAY_ONE, end: datetime = DAY_TWO) -> IngestionReport:
    """Registers the dataset in the catalog and runs the CDAWeb pipeline in one-day chunks against the fake CDAS."""
    time_range = {"start_time": "2024-01-01T00:00:00Z", "end_time": "2024-01-31T00:00:00Z"}
    source = cast(CatalogEntryDict, {"type": "CDAWEB", "source_id": GROUP, "time_range": time_range})
    MongoCatalogRepository(db).save({"dataset_id": DATASET_ID, "sources": [source]})
    pipeline = CdawebPipeline(
        timeseries=MongoTimeSeriesRepository(db),
        catalog_repository=MongoCatalogRepository(db),
        metadata_repository=MongoMetadataRepository(db),
        coverage_repository=MongoCoverageRepository(db),
        chunk_status=MongoChunkStatusRepository(db),
        cdas=cdas,
        chunk_unit=TimeBucket.DAY,
    )
    return pipeline.run([DATASET_ID], start, end)


def _dataset_metadata(db: Database) -> DatasetMetadata:
    """Reads the metadata record of the dataset through the repository."""
    record = MongoMetadataRepository(db).get(DATASET_ID)
    assert record is not None
    return record


def _master_variable(var_type: str, record_variance: str = "VARY", dimension_sizes: list[int] | None = None) -> dict[str, Any]:
    """Builds one variable of a parsed CDAWeb master file."""
    description: dict[str, Any] = {"RecVariance": record_variance}
    if dimension_sizes:
        description["DimSizes"] = dimension_sizes
    return {"description": description, "attributes": {"VAR_TYPE": var_type}}


@pytest.mark.integration
def test_cdaweb_ingestion_stores_one_document_per_record_at_its_epoch(
    db: Database, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """Each record inside the run yields one document at its epoch with the CDAWeb group as source."""
    _ingest(db, FakeCdasWs())

    expected_times = sorted([_record_time(DAY_ONE, record_number) for record_number in range(RECORDS_PER_DAY)] + [DAY_ONE + SLOW_OFFSET])
    assert stored_timestamps_and_metadata(DATASET_ID) == [(time, SOURCE_METADATA) for time in expected_times]


@pytest.mark.integration
def test_cdaweb_chunk_boundary_record_stored_once_and_variable_list_read_once(
    db: Database, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """A run over two chunks stores every record of both chunks once, including the record at the boundary that both responses hold."""
    cdas = FakeCdasWs()

    _ingest(db, cdas, end=DAY_THREE)

    epoch_times = [_record_time(day, record_number) for day in (DAY_ONE, DAY_TWO) for record_number in range(RECORDS_PER_DAY)]
    slow_times = [day + SLOW_OFFSET for day in (DAY_ONE, DAY_TWO)]
    assert [time for time, _ in stored_timestamps_and_metadata(DATASET_ID)] == sorted(epoch_times + slow_times)
    assert (len(cdas.data_calls), cdas.variable_name_calls) == (2, 1)


@pytest.mark.integration
def test_cdaweb_fill_and_out_of_range_values_omit_only_their_fields(db: Database, stored_data: StoredData) -> None:
    """Float and integer fill values and values outside the scalar or per-component valid range are omitted, the other fields kept."""
    _ingest(db, FakeCdasWs())

    float_fill_data = stored_data(DATASET_ID, _record_time(DAY_ONE, 1))
    assert float_fill_data == pytest.approx({"quality": 1, "B_0": 4.0, "B_1": 5.0, "B_2": 6.0})
    assert type(float_fill_data["quality"]) is int
    assert stored_data(DATASET_ID, _record_time(DAY_ONE, 2)) == pytest.approx({"flux": 3.5, "B_0": 7.0, "B_1": 8.0, "B_2": 9.0})
    assert stored_data(DATASET_ID, _record_time(DAY_ONE, 3)) == pytest.approx({"quality": 3, "B_0": 9.0, "B_2": -29.0})


@pytest.mark.integration
def test_cdaweb_variables_on_a_second_time_axis_get_own_documents(db: Database, stored_data: StoredData) -> None:
    """A variable on its own time axis is stored at its own time stamps, and the records of the other axis lack its field."""
    _ingest(db, FakeCdasWs())

    assert stored_data(DATASET_ID, DAY_ONE + SLOW_OFFSET) == {"slow": 0.0}
    assert stored_data(DATASET_ID, _record_time(DAY_ONE, 0)) == pytest.approx({"flux": 1.5, "quality": 0, "B_0": 1.0, "B_1": 2.0, "B_2": 3.0})


@pytest.mark.integration
def test_cdaweb_large_arrays_and_variables_without_time_axis_are_listed_as_not_stored(db: Database) -> None:
    """Arrays above the column limit, arrays with more dimensions and variables whose first dimension is no time axis are listed with their reason."""
    _ingest(db, FakeCdasWs())

    not_stored = {skipped.name: skipped.reason for skipped in _dataset_metadata(db).not_stored}
    assert not_stored == {
        "spectrum": "array with more than 10 elements",
        "image": "array with more than 10 elements",
        "unplaced": "time axis not resolvable",
    }


@pytest.mark.integration
@pytest.mark.parametrize(("bin_location", "expected_location"), [(None, "center"), (0.0, "begin"), (0.25, "other")])
def test_cdaweb_metadata_from_istp_attributes(db: Database, bin_location: float | None, expected_location: str) -> None:
    """Description, units, fill, valid range and VAR_TYPE come from the delivered ISTP attributes; the time stamp location from BIN_LOCATION."""
    _ingest(db, FakeCdasWs(bin_location=bin_location))

    metadata = _dataset_metadata(db)
    variables = {variable.name: variable for variable in metadata.variables}
    flux = variables["flux"]
    assert (flux.description, flux.units, flux.fill, flux.valid_min, flux.valid_max, flux.var_type, flux.dtype) == (
        "Synthetic flux",
        "nT",
        FLOAT_FILL,
        0.0,
        100.0,
        "data",
        "double",
    )
    quality = variables["quality"]
    assert (quality.description, quality.units, quality.fill, quality.dtype) == ("Synthetic quality flag", None, INT_FILL, "integer")
    assert type(quality.fill) is int
    assert set(flux.field_sources.values()) == {"file"}
    assert metadata.time.timestamp_location == expected_location


@pytest.mark.integration
def test_cdaweb_metadata_variables_match_stored_fields(db: Database, stored_data: StoredData) -> None:
    """The record lists one variable per stored field, vector components with their index, and keeps the raw ISTP attributes."""
    _ingest(db, FakeCdasWs())

    metadata = _dataset_metadata(db)
    variables = {variable.name: variable for variable in metadata.variables}
    assert set(variables) == set(stored_data(DATASET_ID, _record_time(DAY_ONE, 0))) | {"slow"}
    components = [(variables[name].source_name, variables[name].component, variables[name].element_index) for name in ("B_0", "B_1", "B_2")]
    assert components == [("B", 0, 0), ("B", 1, 1), ("B", 2, 2)]
    # VALIDMIN of B holds one value per component, so no single valid range applies
    assert variables["B_1"].valid_min is None
    raw = MongoMetadataRepository(db).get_raw(DATASET_ID, MetadataSourceKind.FILE)
    assert raw is not None
    assert raw.payload["B"]["VALIDMIN"] == B_VALID_MIN


@pytest.mark.integration
def test_cdaweb_metadata_keeps_variables_delivered_only_by_earlier_chunks(db: Database) -> None:
    """The record of a run over several chunks describes every variable a chunk delivered, not only those of the last chunk."""
    _ingest(db, FakeCdasWs(slow_until=DAY_TWO), end=DAY_THREE)

    assert "slow" in {variable.name for variable in _dataset_metadata(db).variables}


@pytest.mark.integration
def test_cdaweb_stored_master_file_selects_the_requested_variables(db: Database) -> None:
    """With a stored master file only its small time-varying data variables are requested, and its large or constant ones are listed as not stored."""
    master = {
        "globals": {},
        "variables": {
            "flux": _master_variable("data"),
            "B": _master_variable("data", dimension_sizes=[3]),
            "spectrum": _master_variable("data", dimension_sizes=[SPECTRUM_COMPONENTS]),
            "calibration": _master_variable("data", record_variance="NOVARY"),
            "epoch": _master_variable("support_data"),
        },
    }
    MongoMetadataRepository(db).save_raw(DATASET_ID, MetadataSourceKind.ISTP, master, "https://cdaweb.example.org/master.json", DAY_ONE)
    cdas = FakeCdasWs()

    _ingest(db, cdas)

    assert ([variables for _, variables, _, _ in cdas.data_calls], cdas.variable_name_calls) == ([["flux", "B"]], 0)
    not_stored = {skipped.name: skipped.reason for skipped in _dataset_metadata(db).not_stored}
    assert not_stored == {"spectrum": "array with more than 10 elements", "calibration": "not time-varying"}


@pytest.mark.integration
@pytest.mark.parametrize(("status_code", "expected_gaps"), [pytest.param(404, 0, id="no_data"), pytest.param(503, 1, id="overloaded")])
def test_cdaweb_answer_without_data_stores_nothing(db: Database, status_code: int, expected_gaps: int) -> None:
    """CDAS answering 404 means no data and leaves no gap, while an error status leaves the chunk as gap; neither stores documents."""
    report = _ingest(db, FakeCdasWs(status_code=status_code))

    assert (len(report.gaps), db[DATASET_ID].count_documents({}), MongoMetadataRepository(db).get(DATASET_ID)) == (expected_gaps, 0, None)


# Start of the day before yesterday, whose chunks are not yet settled and are fetched again by every run.
RECENT_DAY = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("start", "expected_new_calls"),
    [pytest.param(DAY_ONE, 0, id="settled-chunks-skipped"), pytest.param(RECENT_DAY, 2, id="recent-chunks-refetched")],
)
def test_repeated_cdaweb_ingestion_stores_nothing_twice(db: Database, start: datetime, expected_new_calls: int) -> None:
    """A second run skips settled chunks and fetches recent ones again, replacing their documents without duplicates."""
    cdas = FakeCdasWs()
    end = start + timedelta(days=2)
    _ingest(db, cdas, start=start, end=end)
    calls_after_first_run = len(cdas.data_calls)
    documents_after_first_run = db[DATASET_ID].count_documents({})

    _ingest(db, cdas, start=start, end=end)

    assert (len(cdas.data_calls) - calls_after_first_run, db[DATASET_ID].count_documents({})) == (expected_new_calls, documents_after_first_run)
