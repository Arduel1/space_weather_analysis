import re
from urllib.parse import urlsplit

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.metadata.models import DatasetMetadata

# Host of the CDAWeb HAPI server, whose datasets CDAS and the CDAWeb master files also describe.
CDAWEB_HOST = "cdaweb.gsfc.nasa.gov"

# The CDAWeb HAPI server splits a dataset with several time variables (ISTP DEPEND_0) into one
# HAPI dataset per time variable, named `{dataset_id}@{index}`; CDAS lists only `{dataset_id}`.
HAPI_SPLIT_SUFFIX = re.compile(r"@\d+$")


def cdas_dataset_id(dataset_id: str) -> str:
    """Returns the dataset ID under which CDAS and the CDAWeb master files list a CDAWeb dataset.

    Args:
        dataset_id: The dataset ID in the master catalog.

    Returns:
        The ID without the HAPI split suffix `@{index}`.
    """
    return HAPI_SPLIT_SUFFIX.sub("", dataset_id)


def is_cdaweb_source(source_type: str | None, server_url: str | None) -> bool:
    """Checks whether a source delivers its data from CDAWeb.

    Args:
        source_type: The source type of a catalog source or metadata record.
        server_url: The server base URL of a HAPI source; ignored for other source types.

    Returns:
        True for sources ingested through SunPy Fido from CDAWeb or from the CDAWeb HAPI server.
    """
    if source_type == SourceType.FIDO_CDAWEB:
        return True
    server_host = urlsplit(server_url or "").hostname or ""
    return source_type == SourceType.HAPI and server_host.lower() == CDAWEB_HOST


def describes_cdaweb_dataset(record: DatasetMetadata) -> bool:
    """Checks whether a metadata record describes a dataset that CDAWeb serves.

    Args:
        record: The metadata record.

    Returns:
        True for records ingested through SunPy Fido from CDAWeb or from the CDAWeb HAPI server.
    """
    return is_cdaweb_source(record.source.type, record.source.server_url)
