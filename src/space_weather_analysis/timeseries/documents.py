from typing import Any

import pandas as pd

from space_weather_analysis.catalog.models import SourceType

# The fields of a stored time series document: the time stamp, the identification of the source and the measured values.
TIME_FIELD = "timestamp"
META_FIELD = "metadata"
DATA_FIELD = "data"
# The keys inside the metadata field: the source identifier and the URL of the server that delivered the values.
SOURCE_KEY = "source"
SERVER_KEY = "server"

# One MongoDB document of a time series collection, built from field names to BSON-compatible values.
type TimeSeriesDocument = dict[str, Any]


def source_identifier(source_type: SourceType, provider_id: str) -> str:
    """Builds the source identifier stored in the metadata field of each document.

    Args:
        source_type: The client family that ingests the source.
        provider_id: The ID of the dataset at its provider.

    Returns:
        The identifier `{source_type}/{provider_id}`.
    """
    return f"{source_type}/{provider_id}"


def frame_to_documents(frame: pd.DataFrame, source: str, server_url: str | None = None) -> list[TimeSeriesDocument]:
    """Builds one time series document per row of a frame indexed by time.

    The missing values of the whole frame are found in one vectorized step; pandas converts the
    kept values to Python types, which BSON can store.

    Args:
        frame: The values, one column per stored data field, with a DatetimeIndex in UTC; a naive index counts as UTC.
        source: The source identifier, stored in the metadata field.
        server_url: The URL of the server that delivered the values, stored in the metadata field when given.

    Returns:
        One document per row with a timezone-aware UTC time stamp; missing values (NaN, NA, None) are left out of its data field.

    Raises:
        ValueError: If the index of the frame is no DatetimeIndex.
    """
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("The frame needs a DatetimeIndex to build time series documents.")
    index = frame.index
    utc_index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    timestamps = utc_index.to_pydatetime()

    metadata: dict[str, str] = {SOURCE_KEY: source}
    if server_url is not None:
        metadata[SERVER_KEY] = server_url

    field_names = [str(column) for column in frame.columns]
    present_values = frame.notna().to_numpy()
    rows = frame.to_dict("records")
    documents = []
    for timestamp, row, row_present in zip(timestamps, rows, present_values, strict=True):
        data = {field_name: value for field_name, value, present in zip(field_names, row.values(), row_present, strict=True) if present}
        # Each document gets its own metadata dict, so a change to one document leaves the others unchanged
        documents.append({TIME_FIELD: timestamp, META_FIELD: dict(metadata), DATA_FIELD: data})
    return documents
