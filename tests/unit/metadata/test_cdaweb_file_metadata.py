from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

from space_weather_analysis.metadata.cdaweb_file import DeliveredMetadata, build_cdaweb_file_metadata, column_data_type
from space_weather_analysis.metadata.models import (
    DatasetMetadata,
    DataType,
    MetadataSource,
    MetadataSourceKind,
    NotStoredReason,
    SkippedParameter,
    SourceInfo,
    TimeInfo,
    TimestampLocation,
    VariableMetadata,
)

DATASET_ID = "SYNTHETIC_H0_MAG"
COLLECTION = "synthetic_h0_mag"
RETRIEVED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
FILE = MetadataSourceKind.FILE


def _epoch_attributes(bin_location: Any = None) -> dict[str, Any]:
    """Builds the attributes of a time variable, with BIN_LOCATION only if given."""
    attributes: dict[str, Any] = {"VAR_TYPE": "support_data", "FIELDNAM": "Time"}
    if bin_location is not None:
        attributes["BIN_LOCATION"] = bin_location
    return attributes


def _build(delivered: DeliveredMetadata) -> DatasetMetadata:
    """Builds the record of the synthetic dataset."""
    return build_cdaweb_file_metadata(DATASET_ID, COLLECTION, delivered, RETRIEVED_AT)


def test_record_describes_each_stored_column_by_its_istp_attributes() -> None:
    """Scalar, vector component and unmatched columns become variables; skipped variables and the time stamp location are recorded."""
    delivered = DeliveredMetadata(
        variable_attributes={
            "Epoch": _epoch_attributes(np.float64(0.0)),
            "B_total": {
                "DEPEND_0": "Epoch",
                "CATDESC": " Magnetic field magnitude ",
                "LABLAXIS": "|B|",
                "UNITS": "nT",
                "FILLVAL": np.array([-1.0e31], dtype=np.float32),
                "VALIDMIN": np.float32(0.0),
                "VALIDMAX": np.int16(5000),
                "VAR_TYPE": "data",
            },
            "B_gse": {
                "DEPEND_0": "Epoch",
                "FIELDNAM": "B GSE",
                "UNITS": " ",
                "FILLVAL": np.int32(-2147483648),
                "VALIDMIN": np.array([-100.0, -100.0, -100.0]),
                "VAR_TYPE": "data",
            },
        },
        column_types={"B_total": DataType.DOUBLE, "B_gse_1": DataType.INTEGER, "quality": DataType.STRING},
        not_stored={"spectrum": SkippedParameter(name="spectrum", reason=NotStoredReason.TOO_MANY_ELEMENTS)},
    )

    expected = DatasetMetadata(
        dataset_id=DATASET_ID,
        collection=COLLECTION,
        source=SourceInfo(type="CDAWEB"),
        time=TimeInfo(timestamp_location=TimestampLocation.BEGIN),
        variables=[
            VariableMetadata.from_single_source(
                FILE,
                name="B_total",
                source_name="B_total",
                description="Magnetic field magnitude",
                label="|B|",
                dtype=DataType.DOUBLE,
                units="nT",
                # The float32 fill value as Python float
                fill=float(np.float32(-1.0e31)),
                valid_min=0.0,
                valid_max=5000.0,
                var_type="data",
            ),
            # A VALIDMIN per component is no single value, so the record leaves it out
            VariableMetadata.from_single_source(
                FILE,
                name="B_gse_1",
                source_name="B_gse",
                component=1,
                element_index=1,
                description="B GSE",
                dtype=DataType.INTEGER,
                fill=-2147483648,
                var_type="data",
            ),
            VariableMetadata.from_single_source(FILE, name="quality", source_name="quality", dtype=DataType.STRING),
        ],
        not_stored=[SkippedParameter(name="spectrum", reason=NotStoredReason.TOO_MANY_ELEMENTS)],
        provenance=[MetadataSource(kind=FILE, retrieved_at=RETRIEVED_AT)],
        updated_at=RETRIEVED_AT,
    )
    assert _build(delivered) == expected


@pytest.mark.parametrize(
    ("time_variables", "expected_location"),
    [
        ({"Epoch": _epoch_attributes()}, TimestampLocation.CENTER),
        ({"Epoch": _epoch_attributes(1.0), "Epoch_2": _epoch_attributes(1.0)}, TimestampLocation.END),
        ({"Epoch": _epoch_attributes(0.0), "Epoch_2": _epoch_attributes(1.0)}, TimestampLocation.OTHER),
        ({"Epoch": _epoch_attributes("start")}, TimestampLocation.OTHER),
    ],
    ids=["absent-is-center", "shared", "differing", "not-a-number"],
)
def test_time_stamp_location_comes_from_bin_location_of_time_variables(time_variables: dict[str, Any], expected_location: TimestampLocation) -> None:
    """The BIN_LOCATION of the DEPEND_0 targets gives the position; absent means center, differing or unknown positions mean other."""
    data_variables = {f"value_{time_name}": {"DEPEND_0": time_name} for time_name in time_variables}
    delivered = DeliveredMetadata(variable_attributes={**time_variables, **data_variables})

    record = _build(delivered)

    assert record.time == TimeInfo(timestamp_location=expected_location)
    assert record.variables == []


@pytest.mark.parametrize(
    ("column", "expected_type"),
    [
        (pd.Series([True, False]), DataType.BOOLEAN),
        (pd.Series([1, None], dtype="Int64"), DataType.INTEGER),
        (pd.Series([1.5, np.nan]), DataType.DOUBLE),
        (pd.Series(["a", None], dtype=object), DataType.STRING),
        (pd.Series(pd.to_datetime(["2024-01-01"])), None),
    ],
    ids=["boolean", "nullable-integer", "double", "object-text", "datetime"],
)
def test_column_data_type_names_the_stored_value_type(column: pd.Series, expected_type: DataType | None) -> None:
    """Boolean, integer, float and text columns map to their data type; other pandas types to None."""
    assert column_data_type(column) == expected_type
