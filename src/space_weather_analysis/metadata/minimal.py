from collections.abc import Iterable
from datetime import datetime

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSource, MetadataSourceKind, SourceInfo, VariableMetadata


def build_minimal_metadata(
    dataset_id: str, collection: str, source_type: SourceType, fields: Iterable[str], server_url: str | None, retrieved_at: datetime
) -> DatasetMetadata:
    """Builds the record of a dataset whose provider publishes no metadata: its source and one variable per stored field.

    The curated metadata, laid over every record on saving, adds title, citation and the description of the fields.

    Args:
        dataset_id: The dataset ID.
        collection: The MongoDB collection holding the data documents.
        source_type: The source type the dataset is ingested from.
        fields: The fields of the stored documents.
        server_url: The location the data were read from; None for a local file.
        retrieved_at: When the data were read.

    Returns:
        The record without descriptions.
    """
    return DatasetMetadata(
        dataset_id=dataset_id,
        collection=collection,
        source=SourceInfo(type=source_type, server_url=server_url),
        variables=[VariableMetadata(name=field, source_name=field) for field in fields],
        provenance=[MetadataSource(kind=MetadataSourceKind.FILE, url=server_url, retrieved_at=retrieved_at)],
        updated_at=retrieved_at,
    )
