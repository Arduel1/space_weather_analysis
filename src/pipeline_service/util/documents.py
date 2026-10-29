from datetime import datetime
from typing import Any, Dict, Hashable, Iterable, List, Mapping, Optional, TypeAlias

import pandas as pd

DocumentDict: TypeAlias = Dict[str, Any]
"""
A dictionary representing a MongoDB document.
Keys are strings (field names), values can be any BSON-compatible type.
"""

TransformedDocuments: TypeAlias = List[DocumentDict]
"""A list of formatted dictionaries ready for MongoDB insertion."""


def dataframe_to_documents(df: pd.DataFrame, source_identifier: str, timestamp_column: Optional[str] = None) -> TransformedDocuments:
    """Converts a pandas DataFrame to a list of standardized MongoDB documents.

    This function provides vectorized conversion of DataFrame rows into
    the standard document structure, handling both indexed and column-based
    timestamps efficiently.

    Args:
        df: The DataFrame to convert. Must have a DatetimeIndex if
            timestamp_column is None.
        source_identifier: String identifying the data source.
        timestamp_column: Optional name of column containing timestamps.
            If None, uses the DataFrame index.

    Returns:
        A list of formatted documents ready for MongoDB insertion.

    Raises:
        ValueError: If timestamp_column is None and index is not DatetimeIndex.
    """
    if timestamp_column is not None:
        if timestamp_column not in df.columns:
            raise ValueError(f"Timestamp column '{timestamp_column}' not found in DataFrame")
        timestamps = pd.to_datetime(df[timestamp_column]).to_pydatetime()  # type: ignore
        data_columns = [col for col in df.columns if col != timestamp_column]
        data_df = df[data_columns]
    else:
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("DataFrame index must be DatetimeIndex when timestamp_column is None")
        timestamps = df.index.to_pydatetime()
        data_df = df

    data_payloads = data_df.to_dict("records")

    return rows_to_documents(timestamps, data_payloads, source_identifier)


def rows_to_documents(timestamps: Iterable[datetime], rows: Iterable[Mapping[Hashable, Any]], source_identifier: str) -> TransformedDocuments:
    """Combines timestamps and data rows into time series documents.

    Each document is built by build_timeseries_document.

    Args:
        timestamps: Time of each row.
        rows: Field values of each row, in the same order as timestamps.
        source_identifier: String identifying the data source.

    Returns:
        One document per row with the fields timestamp, metadata and data.

    Raises:
        ValueError: If timestamps and rows differ in length.
    """
    documents = []
    for timestamp, row in zip(timestamps, rows, strict=True):
        documents.append(build_timeseries_document(timestamp, row, source_identifier))
    return documents


def build_timeseries_document(
    timestamp: datetime,
    data_dict: Mapping[Hashable, Any],
    source_identifier: str,
    additional_metadata: Optional[Dict[str, Any]] = None,
) -> DocumentDict:
    """Builds a standardized time-series document for MongoDB insertion.

    The standard structure places timestamp and metadata at the root level,
    with actual measurements nested under a 'data' field.

    Args:
        timestamp: The timestamp of this data point.
        data_dict: Measurement values; missing values (NaN, NA, None) are left out of the document.
        source_identifier: String identifying the data source.
        additional_metadata: Optional additional metadata fields.

    Returns:
        A formatted document ready for MongoDB insertion.
    """
    metadata = {"source": source_identifier}
    if additional_metadata:
        metadata.update(additional_metadata)

    # Filter out NaN values and ensure keys are strings for MongoDB compatibility
    clean_data = {str(key): value for key, value in data_dict.items() if not pd.isna(value)}

    return {"timestamp": timestamp, "metadata": metadata, "data": clean_data}


def build_gridfs_reference_document(
    timestamp: datetime,
    gridfs_id: Any,
    source_identifier: str,
    additional_metadata: Optional[Dict[str, Any]] = None,
) -> DocumentDict:
    """Builds a document that references data stored in GridFS.

    For multi-dimensional data too large to store directly in documents,
    this function creates a reference document that maintains the same
    structure as regular time-series documents but points to GridFS storage.

    Args:
        timestamp: The timestamp for this data point.
        gridfs_id: The GridFS ObjectId referencing the stored data payload.
        source_identifier: String identifying the data source.
        additional_metadata: Optional additional metadata fields.

    Returns:
        A formatted reference document for MongoDB insertion.
    """
    metadata = {"source": source_identifier}
    if additional_metadata:
        metadata.update(additional_metadata)

    return {
        "timestamp": timestamp,
        "metadata": metadata,
        "data_gridfs_id": gridfs_id,
    }
