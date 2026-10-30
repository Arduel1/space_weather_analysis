import logging
import pathlib
from datetime import datetime
from typing import Any

import requests
from astropy.table import Row, Table
from parfive.results import Results as ParfiveResults
from sunpy.net import Fido
from sunpy.net import attrs as a
from sunpy.net.cdaweb import attrs as cdaweb_attrs
from sunpy.net.cdaweb.helpers import get_datasets, get_observatory_groups
from sunpy.net.fido_factory import UnifiedResponse

from space_weather_analysis.catalog.models import CatalogEntryDict, SourceType
from space_weather_analysis.clients.base import DataClient, FetchResult
from space_weather_analysis.util.error_handling import DataRetrievalError
from space_weather_analysis.util.resilience import retry_interaction

logger = logging.getLogger(__name__)

# Network failures of the CDAWeb requests SunPy sends, which a repeated request may not have.
TRANSIENT_REQUEST_ERRORS = (requests.ConnectionError, requests.Timeout)


class CdawebClient(DataClient[list[pathlib.Path]]):
    """Downloads CDAWeb data files with SunPy Fido and lists observatory groups and datasets for the discovery.

    The data of a range are the downloaded files covering it; the files may hold records outside the
    range, which the caller clips.
    """

    source_type = SourceType.FIDO_CDAWEB

    def __init__(self, download_dir: pathlib.Path | None = None) -> None:
        """Initializes the client.

        Args:
            download_dir: The directory the data files are downloaded to, one subdirectory per dataset;
                None for a client that only serves the discovery.
        """
        self.download_dir = download_dir

    @retry_interaction(exceptions=TRANSIENT_REQUEST_ERRORS)
    def get_observatory_groups(self) -> Table | None:
        """Fetches all observatory groups of CDAWeb.

        Returns:
            The observatory groups, or None if CDAWeb lists none.
        """
        groups = get_observatory_groups()
        if groups is None or len(groups) == 0:
            logger.warning("CDAWeb returned an empty list of observatory groups.")
            return None
        logger.info("Found %d observatory groups.", len(groups))
        return groups

    @retry_interaction(exceptions=TRANSIENT_REQUEST_ERRORS)
    def get_datasets_for_group(self, group_name: str) -> Table | None:
        """Fetches the datasets of one observatory group.

        Args:
            group_name: The name of the group.

        Returns:
            The dataset definitions, or None if the group has none or CDAWeb answers with no JSON.
        """
        logger.info("Fetching datasets for group '%s'...", group_name)
        try:
            datasets = get_datasets(group_name)
        except requests.JSONDecodeError as error:
            response_text = error.response.text if error.response is not None else "no response text"
            logger.warning("CDAWeb answered with no JSON for group '%s': %s. Response: %s", group_name, error, response_text)
            return None
        if datasets is None or len(datasets) == 0:
            logger.info("No datasets found for group '%s'.", group_name)
            return None
        return datasets

    @staticmethod
    def parse_dataset_row(row: Row | dict[str, Any], group_name: str) -> CatalogEntryDict | None:
        """Builds the catalog entry of one dataset row SunPy lists for a group.

        Args:
            row: The dataset row with Id, Start and End.
            group_name: The observatory group of the row.

        Returns:
            The catalog entry, or None if the row lacks a field or a time range.
        """
        try:
            dataset_id = str(row["Id"])
            start_date = row["Start"]
            stop_date = row["End"]
        except KeyError as error:
            logger.warning("Row of group %s lacks the field %s: %s", group_name, error, row)
            return None

        if not start_date or not stop_date:
            logger.warning("Dataset %s (group %s) has no time range; skipped.", dataset_id, group_name)
            return None

        return {
            "type": SourceType.FIDO_CDAWEB,
            "dataset_id": dataset_id,
            "source_id": group_name,
            "time_range": {"start_time": str(start_date), "end_time": str(stop_date)},
        }

    def fetch(self, dataset_id: str, start: datetime, stop: datetime) -> FetchResult[list[pathlib.Path]]:
        """Downloads the data files of a dataset that cover a time range.

        Args:
            dataset_id: The CDAWeb dataset ID.
            start: The start of the range (inclusive), timezone-aware.
            stop: The end of the range (exclusive), timezone-aware.

        Returns:
            The paths of the downloaded files, or a result without data if CDAWeb has no file for the range.

        Raises:
            ValueError: If the client has no download directory.
            DataRetrievalError: If the search or a download failed.
        """
        if self.download_dir is None:
            raise ValueError("CdawebClient needs a download directory to fetch data files.")
        destination = self.download_dir / dataset_id
        destination.mkdir(parents=True, exist_ok=True)

        logger.info("Searching Fido for '%s' from %s to %s...", dataset_id, start, stop)
        try:
            query_response: UnifiedResponse = Fido.search(a.Time(start, stop), cdaweb_attrs.Dataset(dataset_id))
        except (requests.RequestException, OSError) as error:
            raise DataRetrievalError(f"Fido search for {dataset_id} failed: {error}") from error
        if query_response.file_num == 0:
            logger.info("Fido found no file of '%s' for %s to %s.", dataset_id, start, stop)
            return FetchResult(None)

        file_results: ParfiveResults = Fido.fetch(query_response, path=str(destination / "{file}"), progress=False)
        if file_results.errors:
            raise DataRetrievalError(f"Fido could not download {len(file_results.errors)} files of {dataset_id}: {file_results.errors}")

        downloaded_paths = [pathlib.Path(file_path) for file_path in file_results.data]
        logger.info("Downloaded %d files of %s.", len(downloaded_paths), dataset_id)
        return FetchResult(downloaded_paths or None)
