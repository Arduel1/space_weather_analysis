import pathlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest
from cdflib.cdfwrite import CDF as CdfWriter
from cdflib.epochs import CDFepoch
from pymongo.database import Database

from pipeline_service.pipelines.cdaweb import CdawebPipeline
from space_weather_analysis.clients.cdaweb import CdawebClient
from space_weather_analysis.metadata.models import DatasetMetadata
from space_weather_analysis.metadata.repository import MongoMetadataRepository

StoredData = Callable[[str, datetime], dict[str, Any]]
StoredTimestampsAndMetadata = Callable[[str], list[tuple[datetime, dict[str, Any]]]]

DATASET_ID = "SYNTH_MAG_H0"
GROUP = "SYNTH"
SOURCE_METADATA = {"source": f"FIDO_CDAWEB/{GROUP}"}

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


def _record_time(day: datetime, record_number: int) -> datetime:
    """Returns the epoch of one daily record as naive UTC, as pymongo reads it back."""
    return (day + record_number * RECORD_INTERVAL).replace(tzinfo=None)


def _write_daily_cdf(path: pathlib.Path, day: datetime, bin_location: float | None) -> None:
    """Writes an ISTP-like CDF file with the four records of a day."""
    record_times = [day + record_number * RECORD_INTERVAL for record_number in range(RECORDS_PER_DAY)]
    epochs = CDFepoch.compute_tt2000([[time.year, time.month, time.day, time.hour, 0, 0, 0, 0, 0] for time in record_times])

    epoch_attributes: dict[str, Any] = {"FIELDNAM": "Epoch", "VAR_TYPE": "support_data"}
    if bin_location is not None:
        epoch_attributes["BIN_LOCATION"] = np.float64(bin_location)

    writer = CdfWriter(str(path))
    writer.write_globalattrs({"Project": {0: "Synthetic test data"}})
    _write_variable(writer, "Epoch", CdfWriter.CDF_TIME_TT2000, np.asarray(epochs), epoch_attributes)
    _write_variable(
        writer,
        "flux",
        CdfWriter.CDF_DOUBLE,
        np.array(FLUX_VALUES),
        {
            "CATDESC": "Synthetic flux",
            "DEPEND_0": "Epoch",
            "FILLVAL": np.float64(FLOAT_FILL),
            "UNITS": "nT",
            "VALIDMIN": np.float64(0.0),
            "VALIDMAX": np.float64(100.0),
            "VAR_TYPE": "data",
        },
    )
    _write_variable(
        writer,
        "quality",
        CdfWriter.CDF_INT4,
        np.array(QUALITY_VALUES, dtype=np.int32),
        {"CATDESC": "Synthetic quality flag", "DEPEND_0": "Epoch", "FILLVAL": np.int32(INT_FILL), "UNITS": " ", "VAR_TYPE": "support_data"},
    )
    _write_variable(
        writer,
        "B",
        CdfWriter.CDF_DOUBLE,
        np.array(B_VALUES),
        {
            "CATDESC": "Synthetic magnetic field vector",
            "DEPEND_0": "Epoch",
            "FILLVAL": np.float64(FLOAT_FILL),
            "UNITS": "nT",
            "VALIDMIN": np.array(B_VALID_MIN),
            "VALIDMAX": np.array(B_VALID_MAX),
            "VAR_TYPE": "data",
        },
        dimension_sizes=[3],
    )
    _write_variable(
        writer,
        "spectrum",
        CdfWriter.CDF_DOUBLE,
        np.ones((RECORDS_PER_DAY, SPECTRUM_COMPONENTS)),
        {"CATDESC": "Synthetic spectrum", "DEPEND_0": "Epoch", "UNITS": "counts", "VAR_TYPE": "data"},
        dimension_sizes=[SPECTRUM_COMPONENTS],
    )
    writer.close()


def _write_variable(
    writer: CdfWriter, name: str, data_type: int, values: np.ndarray, attributes: dict[str, Any], dimension_sizes: list[int] | None = None
) -> None:
    """Writes one record-varying variable with its attributes."""
    specification = {"Variable": name, "Data_Type": data_type, "Num_Elements": 1, "Rec_Vary": True, "Dim_Sizes": dimension_sizes or []}
    writer.write_var(specification, var_attrs=attributes, var_data=values)


class FakeCdawebClient(CdawebClient):
    """CDAWeb client writing synthetic daily CDF files instead of downloading via Fido, recording its calls."""

    def __init__(self, bin_location: float | None = None, delivers_files: bool = True) -> None:
        """Starts without calls; delivers_files False makes every request return no files."""
        super().__init__()
        self.bin_location = bin_location
        self.delivers_files = delivers_files
        self.calls: list[tuple[str, datetime, datetime]] = []

    def get_data(self, dataset_id: str, start_time: datetime, end_time: datetime, **kwargs: Any) -> list[pathlib.Path]:
        """Returns one file per day touching [start_time, end_time], as Fido delivers whole daily files."""
        self.calls.append((dataset_id, start_time, end_time))
        if not self.delivers_files:
            return []

        download_path = pathlib.Path(kwargs["download_path"])
        day = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
        file_paths = []
        while day <= end_time:
            file_path = download_path / f"{dataset_id.lower()}_{day:%Y%m%d}_v01.cdf"
            if not file_path.exists():
                _write_daily_cdf(file_path, day, self.bin_location)
            file_paths.append(file_path)
            day += timedelta(days=1)
        return file_paths


@pytest.fixture(autouse=True)
def one_day_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pipeline chunks of one day, so short runs cover several chunks."""
    monkeypatch.setattr(CdawebPipeline, "CHUNK_DURATION", timedelta(days=1))


def _ingest(db: Database, tmp_path: pathlib.Path, client: FakeCdawebClient, start: datetime = DAY_ONE, end: datetime = DAY_TWO) -> None:
    """Registers the dataset in the catalog, unless already there, and runs the CDAWeb pipeline with the fake client."""
    if db["master_catalog"].count_documents({"dataset_id": DATASET_ID}) == 0:
        source = {"type": "FIDO_CDAWEB", "source_id": GROUP, "time_range": {"start_time": "2024-01-01T00:00:00Z", "end_time": "2024-01-31T00:00:00Z"}}
        db["master_catalog"].insert_one({"dataset_id": DATASET_ID, "sources": [source]})
    CdawebPipeline(db, tmp_path / "downloads", client=client).run([DATASET_ID], start, end)


def _dataset_metadata(db: Database) -> DatasetMetadata:
    """Reads the metadata record of the dataset through the repository."""
    record = MongoMetadataRepository(db).get(DATASET_ID)
    assert record is not None
    return record


@pytest.mark.integration
def test_cdaweb_ingestion_stores_one_document_per_record_at_its_epoch(
    db: Database, tmp_path: pathlib.Path, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """Each record inside the run yields one document at its epoch (naive UTC) with the CDAWeb group as source."""
    _ingest(db, tmp_path, FakeCdawebClient())

    expected_times = [_record_time(DAY_ONE, record_number) for record_number in range(RECORDS_PER_DAY)]
    assert stored_timestamps_and_metadata(DATASET_ID) == [(time, SOURCE_METADATA) for time in expected_times]


@pytest.mark.integration
def test_cdaweb_chunk_boundary_record_stored_once_and_next_chunk_processed(
    db: Database, tmp_path: pathlib.Path, stored_timestamps_and_metadata: StoredTimestampsAndMetadata
) -> None:
    """A run over two chunks stores every record of both chunks once, including the record at the boundary, which both downloads contain."""
    client = FakeCdawebClient()

    _ingest(db, tmp_path, client, end=DAY_THREE)

    assert len(client.calls) == 2
    expected_times = [_record_time(day, record_number) for day in (DAY_ONE, DAY_TWO) for record_number in range(RECORDS_PER_DAY)]
    assert [time for time, _ in stored_timestamps_and_metadata(DATASET_ID)] == expected_times


@pytest.mark.integration
def test_cdaweb_fill_values_omit_only_their_fields(db: Database, tmp_path: pathlib.Path, stored_data: StoredData) -> None:
    """Float and integer fill values are omitted while the other fields are kept, integers as int."""
    _ingest(db, tmp_path, FakeCdawebClient())

    float_fill_data = stored_data(DATASET_ID, _record_time(DAY_ONE, 1))
    assert float_fill_data == pytest.approx({"quality": 1, "B_0": 4.0, "B_1": 5.0, "B_2": 6.0})
    assert type(float_fill_data["quality"]) is int
    assert stored_data(DATASET_ID, _record_time(DAY_ONE, 2)) == pytest.approx({"flux": 3.5, "B_0": 7.0, "B_1": 8.0, "B_2": 9.0})


@pytest.mark.integration
def test_cdaweb_values_outside_valid_range_are_omitted(db: Database, tmp_path: pathlib.Path, stored_data: StoredData) -> None:
    """Values outside the scalar or per-component VALIDMIN/VALIDMAX are omitted while the other fields of the record are kept."""
    _ingest(db, tmp_path, FakeCdawebClient())

    assert stored_data(DATASET_ID, _record_time(DAY_ONE, 3)) == pytest.approx({"quality": 3, "B_0": 9.0, "B_2": -29.0})


@pytest.mark.integration
def test_cdaweb_vector_columns_stored_and_large_array_not_stored(db: Database, tmp_path: pathlib.Path, stored_data: StoredData) -> None:
    """A 3-component vector is stored as B_0 to B_2; the 12-component variable is not stored and listed in the metadata."""
    _ingest(db, tmp_path, FakeCdawebClient(), end=DAY_THREE)

    assert stored_data(DATASET_ID, _record_time(DAY_ONE, 0)) == pytest.approx({"flux": 1.5, "quality": 0, "B_0": 1.0, "B_1": 2.0, "B_2": 3.0})
    assert [skipped.name for skipped in _dataset_metadata(db).not_stored] == ["spectrum"]


@pytest.mark.integration
@pytest.mark.parametrize(("bin_location", "expected_location"), [(None, "center"), (0.0, "begin"), (0.25, "other")])
def test_cdaweb_metadata_from_istp_attributes(db: Database, tmp_path: pathlib.Path, bin_location: float | None, expected_location: str) -> None:
    """Description, units, fill, valid range and VAR_TYPE come from the ISTP attributes; the time stamp location from BIN_LOCATION."""
    _ingest(db, tmp_path, FakeCdawebClient(bin_location=bin_location))

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
def test_cdaweb_metadata_variables_match_stored_fields(db: Database, tmp_path: pathlib.Path, stored_data: StoredData) -> None:
    """The record lists one variable per stored field, vector components with their index, and keeps the raw ISTP attributes."""
    _ingest(db, tmp_path, FakeCdawebClient())

    metadata = _dataset_metadata(db)
    variables = {variable.name: variable for variable in metadata.variables}
    assert set(variables) == set(stored_data(DATASET_ID, _record_time(DAY_ONE, 0)))
    components = [(variables[name].source_name, variables[name].component, variables[name].element_index) for name in ("B_0", "B_1", "B_2")]
    assert components == [("B", 0, 0), ("B", 1, 1), ("B", 2, 2)]
    # VALIDMIN of B holds one value per component, so no single valid range applies
    assert variables["B_1"].valid_min is None
    raw = MongoMetadataRepository(db).get_raw(DATASET_ID, "file")
    assert raw is not None
    assert raw.payload["B"]["VALIDMIN"] == B_VALID_MIN


@pytest.mark.integration
def test_repeated_cdaweb_ingestion_skips_chunks_with_documents(db: Database, tmp_path: pathlib.Path) -> None:
    """A second run requests no files for chunks that already hold documents and stores nothing twice."""
    client = FakeCdawebClient()
    _ingest(db, tmp_path, client, end=DAY_THREE)
    calls_after_first_run = len(client.calls)

    _ingest(db, tmp_path, client, end=DAY_THREE)

    assert len(client.calls) == calls_after_first_run
    assert db[DATASET_ID].count_documents({}) == 2 * RECORDS_PER_DAY


@pytest.mark.integration
def test_cdaweb_without_downloaded_files_stores_nothing(db: Database, tmp_path: pathlib.Path) -> None:
    """A chunk without downloaded files stores no documents and no metadata and raises no exception."""
    client = FakeCdawebClient(delivers_files=False)

    _ingest(db, tmp_path, client)

    assert len(client.calls) == 1
    assert db[DATASET_ID].count_documents({}) == 0
    assert MongoMetadataRepository(db).get(DATASET_ID) is None
