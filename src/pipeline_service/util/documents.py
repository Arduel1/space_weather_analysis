from datetime import datetime
from typing import Any, Dict, Hashable, List, Optional

import pandas as pd


def dataframe_to_documents(df: pd.DataFrame, source_identifier: str, timestamp_column: Optional[str] = None) -> List[Dict[str, Any]]:
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
    metadata = {"source": source_identifier}

    # Using list comprehension for efficiency
    documents = [
        {
            "timestamp": ts,
            "metadata": metadata,
            "data": {str(k): v for k, v in data.items() if not pd.isna(v)},
        }
        for ts, data in zip(timestamps, data_payloads, strict=False)
    ]

    return documents


def build_timeseries_document(
    timestamp: datetime,
    data_dict: Dict[Hashable, Any],
    source_identifier: str,
    additional_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Builds a standardized time-series document for MongoDB insertion.

    The standard structure places timestamp and metadata at the root level,
    with actual measurements nested under a 'data' field.

    Args:
        timestamp: The timestamp for this data point (must be timezone-aware).
        data_dict: Dictionary of measurement values (excludes NaN values).
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
) -> Dict[str, Any]:
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


def build_metadata_document(dataset_id: str, source_info: Dict[str, Any], variables: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Builds a standardized metadata document for dataset description.

    Metadata documents use the dataset_id as their MongoDB _id to enable
    efficient lookups and ensure one metadata document per dataset.

    Args:
        dataset_id: Unique identifier for the dataset (becomes _id).
        source_info: Dictionary describing the data source.
        variables: List of variable metadata dictionaries.

    Returns:
        A formatted metadata document for MongoDB insertion.
    """
    return {"_id": dataset_id, "source_info": source_info, "variables": variables}
