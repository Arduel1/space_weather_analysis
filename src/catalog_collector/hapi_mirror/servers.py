from typing import Any

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.metadata.models import DatasetMetadata


def describes_hapi_source(record: DatasetMetadata, server_url: str) -> bool:
    """Checks whether a metadata record was built from the HAPI server of a catalog source.

    Records of other source kinds rank above HAPI metadata, so the mirror never overwrites them.

    Args:
        record: The stored metadata record.
        server_url: The base URL of the HAPI source in the catalog.

    Returns:
        True if the record is HAPI metadata of this server or of an unnamed server.
    """
    if record.source.type != SourceType.HAPI:
        return False
    return not record.source.server_url or normalize_server_url(record.source.server_url) == normalize_server_url(server_url)


def normalize_server_url(url: str) -> str:
    """Normalizes a server base URL for comparison, ignoring scheme, case and trailing slashes.

    Args:
        url: The server base URL.

    Returns:
        The URL without `http://` or `https://`, in lower case and without trailing slash.
    """
    normalized = url.strip().lower()
    for scheme in ("https://", "http://"):
        if normalized.startswith(scheme):
            normalized = normalized[len(scheme) :]
            break
    return normalized.rstrip("/")


def index_mirror_servers(catalogs: dict[str, Any]) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Reads base URL and listed dataset IDs of each server from a mirror file with one entry per server.

    Args:
        catalogs: The parsed catalogs.json or catalogs-all.json: `{server_id}` mapped to its about and catalog responses.

    Returns:
        The mirror server ID keyed by the normalized server base URL, and the dataset IDs of each mirror server.

    Raises:
        AttributeError: If a server entry lacks its expected structure.
    """
    server_ids_by_url: dict[str, str] = {}
    listed_datasets: dict[str, set[str]] = {}
    for server_id, server_entry in catalogs.items():
        server_url = server_entry.get("about", {}).get("x_url")
        if server_url:
            server_ids_by_url[normalize_server_url(server_url)] = server_id
        catalog_entries = server_entry.get("catalog", {}).get("catalog") or []
        listed_datasets[server_id] = {entry["id"] for entry in catalog_entries if isinstance(entry, dict) and "id" in entry}
    return server_ids_by_url, listed_datasets


def mirror_server_id(server_url: str, dataset_id: str, server_ids_by_url: dict[str, str], listed_datasets: dict[str, set[str]]) -> str | None:
    """Finds the mirror server ID of a catalog server.

    Some servers are reachable under several paths of the same host, and the mirror lists only one of them
    (iSWA as `iswa.gsfc.nasa.gov/hapi` while the catalog holds `iswa.gsfc.nasa.gov/IswaSystemWebApp/hapi`).
    Without an exact URL match, the only mirror server of the same host that lists the dataset is used.

    Args:
        server_url: The base URL of the HAPI server in the catalog.
        dataset_id: The dataset ID of the catalog source.
        server_ids_by_url: The mirror server ID, keyed by the normalized server base URL.
        listed_datasets: The dataset IDs of each mirror server.

    Returns:
        The mirror server ID, or None if the mirror does not maintain the server or lists no dataset for it.
    """
    normalized_url = normalize_server_url(server_url)
    server_id = server_ids_by_url.get(normalized_url)
    if server_id is None:
        host = normalized_url.split("/", 1)[0]
        same_host_servers = []
        for mirror_url, candidate_server_id in server_ids_by_url.items():
            if mirror_url.split("/", 1)[0] == host and dataset_id in listed_datasets.get(candidate_server_id, set()):
                same_host_servers.append(candidate_server_id)
        server_id = same_host_servers[0] if len(same_host_servers) == 1 else None
    # A server without any dataset in the mirror says nothing about single datasets
    if server_id is None or not listed_datasets.get(server_id):
        return None
    return server_id


def first_hapi_servers(catalog_repository: CatalogRepository) -> dict[str, str]:
    """Reads the first HAPI server of each dataset from the master catalog, the one server its HAPI metadata record describes.

    Every writer of HAPI records builds them for this server, so the snapshot load, the mirror refresh and the coverage
    sync agree on which record belongs to which source; the server a response came from is kept only in the raw metadata.

    Args:
        catalog_repository: The master catalog.

    Returns:
        The base URL of the first HAPI source, keyed by dataset ID; datasets without HAPI source are left out.
    """
    hapi_servers: dict[str, str] = {}
    for dataset_id, sources in catalog_repository.get_source_map(SourceType.HAPI).items():
        for source in sources:
            if source.get("source_id"):
                hapi_servers[dataset_id] = source["source_id"]
                break
    return hapi_servers
