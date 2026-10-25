import pathlib
import shutil
from datetime import datetime
from typing import Any, Callable, Dict, List, NamedTuple, Tuple

import pytest
from pymongo.database import Database

# The files in data/ hold invented values in the file layout of their data provider.
DATA_DIR = pathlib.Path(__file__).parent / "data"


class _InputFile(NamedTuple):
    """Synthetic file name in data/ and the file name LocalPipeline expects."""

    source_name: str
    target_name: str


_INPUT_FILES: Dict[str, _InputFile] = {
    "silso_sunspot_daily": _InputFile("synthetic_sunspots.csv", "SN_d_tot_V2.0.csv"),
    "cme_catalog": _InputFile("synthetic_cme_catalog.txt", "cme_catalog.txt"),
}


def _copy_input_file(directory: pathlib.Path, dataset_id: str) -> pathlib.Path:
    """Copies the synthetic input file of a dataset into a directory and returns its path."""
    input_file = _INPUT_FILES[dataset_id]
    target = directory / input_file.target_name
    shutil.copyfile(DATA_DIR / input_file.source_name, target)
    return target


@pytest.fixture
def write_dataset_input() -> Callable[[pathlib.Path, str], None]:
    """Function copying the synthetic input file of a dataset into a directory."""

    def write(directory: pathlib.Path, dataset_id: str) -> None:
        _copy_input_file(directory, dataset_id)

    return write


@pytest.fixture
def data_dir_with_extra_row(tmp_path: pathlib.Path) -> Callable[[str, str], pathlib.Path]:
    """Function building a data directory with the input file of a dataset plus one appended row."""

    def build(dataset_id: str, row: str) -> pathlib.Path:
        input_file = _copy_input_file(tmp_path, dataset_id)
        with input_file.open("a", encoding="utf-8") as file:
            file.write(row + "\n")
        return tmp_path

    return build


@pytest.fixture
def sunspot_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Data directory with only the synthetic sunspot file."""
    _copy_input_file(tmp_path, "silso_sunspot_daily")
    return tmp_path


@pytest.fixture
def cme_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Data directory with only the synthetic CME catalog."""
    _copy_input_file(tmp_path, "cme_catalog")
    return tmp_path


@pytest.fixture
def full_data_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Data directory with the synthetic input file of every local dataset."""
    for dataset_id in _INPUT_FILES:
        _copy_input_file(tmp_path, dataset_id)
    return tmp_path


@pytest.fixture
def stored_timestamps_and_metadata(db: Database) -> Callable[[str], List[Tuple[datetime, Dict[str, Any]]]]:
    """Function reading timestamp and metadata of every document of a collection, sorted by timestamp."""

    def read(collection_name: str) -> List[Tuple[datetime, Dict[str, Any]]]:
        return [(doc["timestamp"], doc["metadata"]) for doc in db[collection_name].find().sort("timestamp", 1)]

    return read


@pytest.fixture
def stored_data(db: Database) -> Callable[[str, datetime], Dict[str, Any]]:
    """Function reading the 'data' field of the single document with a given timestamp."""

    def read(collection_name: str, timestamp: datetime) -> Dict[str, Any]:
        documents = list(db[collection_name].find({"timestamp": timestamp}))
        assert len(documents) == 1
        data: Dict[str, Any] = documents[0]["data"]
        return data

    return read
