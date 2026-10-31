from collections.abc import Sequence
from datetime import datetime
from typing import Any

import pandas as pd

# The fields of a stored time series document: the time stamp, the identification of the source and the measured values.
TIME_FIELD = "timestamp"
META_FIELD = "metadata"
DATA_FIELD = "data"
# The key of the source identifier inside the metadata field.
SOURCE_KEY = "source"

# One MongoDB document of a time series collection, built from field names to BSON-compatible values.
type DocumentDict = dict[str, Any]


def dataframe_to_documents(frame: pd.DataFrame, source_identifier: str) -> list[DocumentDict]:
    """Builds one time series document per row of a frame indexed by time.

    Args:
        frame: The values, one column per stored field, with a DatetimeIndex.
        source_identifier: The identification of the source, stored in the metadata field.

    Returns:
        One document per row; missing values are left out of its data field.

    Raises:
        ValueError: If the index of the frame is no DatetimeIndex.
    """
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("The frame needs a DatetimeIndex to build time series documents.")
    return frame_to_documents(list(frame.index.to_pydatetime()), frame, source_identifier)


def frame_to_documents(timestamps: Sequence[datetime], frame: pd.DataFrame, source_identifier: str) -> list[DocumentDict]:
    """Builds one time series document per row of a frame, at the time stamp of the same position.

    The missing values of the whole frame are found in one vectorized step; pandas converts the
    kept values to Python types, which BSON can store.

    Args:
        timestamps: The time of each row.
        frame: The values, one column per stored field.
        source_identifier: The identification of the source, stored in the metadata field.

    Returns:
        One document per row; missing values (NaN, NA, None) are left out of its data field.

    Raises:
        ValueError: If timestamps and rows differ in number.
    """
    field_names = [str(column) for column in frame.columns]
    present_values = frame.notna().to_numpy()
    rows = frame.to_dict("records")
    documents = []
    for timestamp, row, row_present in zip(timestamps, rows, present_values, strict=True):
        data = {field_name: value for field_name, value, present in zip(field_names, row.values(), row_present, strict=True) if present}
        documents.append({TIME_FIELD: timestamp, META_FIELD: {SOURCE_KEY: source_identifier}, DATA_FIELD: data})
    return documents


def build_gridfs_reference_document(
    timestamp: datetime,
    gridfs_id: Any,
    source_identifier: str,
    additional_metadata: dict[str, Any] | None = None,
) -> DocumentDict:
    """Builds a document that references data stored in GridFS.

    The document has the structure of a time series document, with a reference to the GridFS file in place of the data field.

    Args:
        timestamp: The time stamp of the data.
        gridfs_id: The GridFS ObjectId of the stored data payload.
        source_identifier: The identification of the source, stored in the metadata field.
        additional_metadata: Further metadata fields.

    Returns:
        The reference document.
    """
    metadata = {SOURCE_KEY: source_identifier}
    if additional_metadata:
        metadata.update(additional_metadata)
    return {TIME_FIELD: timestamp, META_FIELD: metadata, "data_gridfs_id": gridfs_id}
