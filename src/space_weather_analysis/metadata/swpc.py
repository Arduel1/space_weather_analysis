from datetime import datetime

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.swpc import SWPC_PRODUCTS
from space_weather_analysis.metadata.models import (
    CitationInfo,
    DatasetMetadata,
    MetadataSource,
    MetadataSourceKind,
    SourceInfo,
    TimeInfo,
    VariableMetadata,
)
from space_weather_analysis.util.time_utils import format_iso_duration

# Provider and terms of the SWPC products, works of the U.S. government
SWPC_PROVIDER = "NOAA Space Weather Prediction Center"
SWPC_LICENSE = "Public domain (U.S. government work)"


def build_swpc_metadata(dataset_id: str, collection: str, stored_fields: list[str], server_url: str, retrieved_at: datetime) -> DatasetMetadata:
    """Builds the metadata record of an SWPC dataset from the product table of the SWPC client.

    SWPC publishes no metadata, so title, cadence and the description and unit of each field are curated there.

    Args:
        dataset_id: The dataset ID, a key of SWPC_PRODUCTS.
        collection: The MongoDB collection holding the data documents.
        stored_fields: The fields of the stored documents; fields the product table does not describe get no description.
        server_url: The URL of the fetched file.
        retrieved_at: When the file was fetched.

    Returns:
        The metadata record with one variable per stored field.

    Raises:
        KeyError: If SWPC_PRODUCTS has no dataset of the ID.
    """
    product = SWPC_PRODUCTS[dataset_id]
    variables = []
    for field_name in stored_fields:
        described = product.variables.get(field_name)
        variables.append(
            VariableMetadata.from_single_source(
                MetadataSourceKind.CURATED,
                name=field_name,
                source_name=field_name,
                description=described.description if described is not None else None,
                units=described.units if described is not None else None,
            )
        )
    return DatasetMetadata(
        dataset_id=dataset_id,
        collection=collection,
        source=SourceInfo(type=SourceType.SWPC, provider=SWPC_PROVIDER, server_url=server_url),
        title=product.title,
        time=TimeInfo(cadence=format_iso_duration(product.cadence)),
        citation=CitationInfo(license=SWPC_LICENSE),
        variables=variables,
        provenance=[MetadataSource(kind=MetadataSourceKind.CURATED, url=server_url, retrieved_at=retrieved_at)],
        updated_at=retrieved_at,
    )
