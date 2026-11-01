from datetime import datetime
from typing import Any

from space_weather_analysis.timeseries.documents import META_FIELD, SOURCE_KEY, TIME_FIELD, TimeSeriesDocument


def build_gridfs_reference_document(
    timestamp: datetime,
    gridfs_id: Any,
    source_identifier: str,
    additional_metadata: dict[str, Any] | None = None,
) -> TimeSeriesDocument:
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
