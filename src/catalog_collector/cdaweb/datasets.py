import logging
from dataclasses import dataclass
from typing import Any

import requests

from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.clients.download import download_json
from space_weather_analysis.metadata.normalize import optional_text
from space_weather_analysis.metadata.overlays.cdas import CdasTimeInterval, cdas_time_interval
from space_weather_analysis.metadata.overlays.cdaweb import cdas_dataset_id, is_cdaweb_source
from space_weather_analysis.util.error_handling import DataProcessingError

logger = logging.getLogger(__name__)

# CDAS, the CDAWeb Coordinated Data Analysis System (https://cdaweb.gsfc.nasa.gov/WebServices/REST/), lists every
# CDAWeb dataset with its time range, DOI, SPASE resource ID and notes page in one response of about 3 MB.
CDAS_DATASETS_URL = "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys/datasets"

# Connect and read timeout in seconds.
CDAS_TIMEOUT = (10.0, 120.0)

# The AdditionalMetadata type under which CDAS links the master file of a dataset.
MASTER_FILE_METADATA_TYPE = "SPDF_SKT_CDF"


@dataclass(frozen=True)
class CdasDataset:
    """One dataset entry of the CDAS dataset list.

    Attributes:
        dataset_id: The CDAWeb dataset ID.
        time_interval: The time range of the dataset; None if the entry has no parsable time range.
        entry: The entry as listed by CDAS.
    """

    dataset_id: str
    time_interval: CdasTimeInterval | None
    entry: dict[str, Any]


def fetch_cdas_datasets(session: requests.Session, url: str = CDAS_DATASETS_URL) -> dict[str, CdasDataset]:
    """Downloads and parses the CDAS dataset list.

    Args:
        session: The HTTP session for the request.
        url: The location of the dataset list.

    Returns:
        The entries keyed by dataset ID; entries without ID are left out.

    Raises:
        DataRetrievalError: If the download fails; carries the Retry-After wait of HTTP 429 and 503.
        DataProcessingError: If the response is no dataset list.
    """
    response = download_json(session, url, CDAS_TIMEOUT, "CDAS dataset list")
    entries = response.get("DatasetDescription") if isinstance(response, dict) else None
    if not isinstance(entries, list):
        raise DataProcessingError(f"CDAS dataset list {url} holds no list of datasets")

    datasets: dict[str, CdasDataset] = {}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("Id"), str):
            datasets[entry["Id"]] = CdasDataset(entry["Id"], cdas_time_interval(entry), entry)
    logger.info("CDAS lists %d datasets.", len(datasets))
    return datasets


def master_file_url(entry: dict[str, Any]) -> str | None:
    """Reads the location of the master file of a dataset from its CDAS entry.

    Args:
        entry: The dataset entry as listed by CDAS.

    Returns:
        The URL of the master file in JSON format, or None if the entry links none.
    """
    for additional_metadata in entry.get("AdditionalMetadata") or []:
        if isinstance(additional_metadata, dict) and additional_metadata.get("Type") == MASTER_FILE_METADATA_TYPE:
            return optional_text(additional_metadata.get("value"))
    return None


def cdaweb_dataset_ids_by_cdas_id(catalog_repository: CatalogRepository) -> dict[str, list[str]]:
    """Lists the catalog datasets that CDAWeb serves, grouped by their CDAS dataset ID.

    Args:
        catalog_repository: The master catalog.

    Returns:
        The catalog dataset IDs keyed by CDAS dataset ID; HAPI splits `{dataset_id}@{index}` share one key.
    """
    datasets: dict[str, list[str]] = {}
    for item in catalog_repository.get_all():
        if any(is_cdaweb_source(source.get("type"), source.get("source_id")) for source in item["sources"]):
            datasets.setdefault(cdas_dataset_id(item["dataset_id"]), []).append(item["dataset_id"])
    return datasets
