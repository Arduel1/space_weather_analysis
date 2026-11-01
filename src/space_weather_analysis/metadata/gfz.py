from collections.abc import Mapping
from datetime import datetime
from typing import Any

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.gfz import GFZ_INDICES, STATUS_MEANINGS
from space_weather_analysis.metadata.models import (
    CitationInfo,
    DatasetMetadata,
    DataType,
    MetadataSource,
    MetadataSourceKind,
    SourceInfo,
    TimeInfo,
    TimestampLocation,
    VariableMetadata,
)
from space_weather_analysis.util.time_utils import format_iso_duration


def build_gfz_metadata(
    dataset_id: str, collection: str, response_meta: Mapping[str, Any], has_status: bool, server_url: str, retrieved_at: datetime
) -> DatasetMetadata:
    """Builds the metadata record of a GFZ Potsdam geomagnetic index.

    Description, cadence and status meanings come from the index table of the GFZ client; license and
    provider come from the meta object GFZ sends with every response.

    Args:
        dataset_id: The GFZ index name, a key of GFZ_INDICES.
        collection: The MongoDB collection holding the index.
        response_meta: The meta object of a fetched response.
        has_status: Whether the stored documents carry the status field of each value.
        server_url: The base URL of the GFZ API.
        retrieved_at: When the response was fetched.

    Returns:
        The metadata record with the index variable and, if has_status is set, the status variable.

    Raises:
        KeyError: If GFZ_INDICES has no index of the name.
    """
    index = GFZ_INDICES[dataset_id]
    variables = [
        VariableMetadata.from_single_source(
            MetadataSourceKind.CURATED, name=dataset_id, source_name=dataset_id, description=index.description, dtype=DataType.DOUBLE
        )
    ]
    if has_status:
        variables.append(
            VariableMetadata.from_single_source(
                MetadataSourceKind.CURATED,
                name="status",
                source_name="status",
                description="Status of the index value",
                dtype=DataType.STRING,
                flag_meanings={str(status): meaning for status, meaning in STATUS_MEANINGS.items()},
            )
        )
    return DatasetMetadata(
        dataset_id=dataset_id,
        collection=collection,
        source=SourceInfo(type=SourceType.GFZ_POTSDAM, provider=response_meta.get("source"), server_url=server_url),
        # GFZ time stamps mark the start of each interval
        time=TimeInfo(cadence=format_iso_duration(index.cadence), timestamp_location=TimestampLocation.BEGIN),
        citation=CitationInfo(license=response_meta.get("license")),
        variables=variables,
        provenance=[
            MetadataSource(kind=MetadataSourceKind.API, url=server_url, retrieved_at=retrieved_at),
            MetadataSource(kind=MetadataSourceKind.CURATED, retrieved_at=retrieved_at),
        ],
        updated_at=retrieved_at,
    )
