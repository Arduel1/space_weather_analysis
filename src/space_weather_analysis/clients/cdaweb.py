import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from http import HTTPStatus
from typing import Any

import requests
import xarray as xr
from astropy.table import Row, Table
from cdasws import CdasWs
from cdasws.datarepresentation import DataRepresentation
from sunpy.net.cdaweb.helpers import get_datasets, get_observatory_groups

from space_weather_analysis.catalog.models import CatalogEntryDict, SourceType
from space_weather_analysis.clients.base import DataClient, FetchResult
from space_weather_analysis.util.error_handling import DataProcessingError, DataRetrievalError
from space_weather_analysis.util.resilience import retry_interaction

logger = logging.getLogger(__name__)

# Network failures of the CDAWeb requests SunPy sends, which a repeated request may not have.
TRANSIENT_REQUEST_ERRORS = (requests.ConnectionError, requests.Timeout)

# The CDAS endpoint cdasws requests by default.
CDAS_ENDPOINT = "https://cdaweb.gsfc.nasa.gov/WS/cdasr/1/dataviews/sp_phys/"

# CDAS answers HTTP 404 for a time range that holds no data of the requested variables.
CDAS_NO_DATA_STATUS = HTTPStatus.NOT_FOUND


class CdawebClient(DataClient[xr.Dataset]):
    """Fetches CDAWeb data from the CDAS web services and lists observatory groups and datasets for the discovery.

    CDAS, the Coordinated Data Analysis System of CDAWeb (https://cdaweb.gsfc.nasa.gov/WebServices/REST/),
    serves the data of a dataset subset by variables and time range; cdasws is its official Python client.
    """

    source_type = SourceType.FIDO_CDAWEB

    def __init__(self, cdas: CdasWs | None = None, variables_for: Callable[[str], Sequence[str] | None] | None = None) -> None:
        """Initializes the client.

        Args:
            cdas: The CDAS web service client; a new one without response cache if None.
            variables_for: Names the variables to request for a dataset, or None to request the variables CDAS lists.
        """
        self.cdas = cdas if cdas is not None else CdasWs(disable_cache=True)
        self.variables_for = variables_for
        self._listed_variables: dict[str, Sequence[str]] = {}

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

    def fetch(self, dataset_id: str, start: datetime, stop: datetime) -> FetchResult[xr.Dataset]:
        """Fetches the data variables of a dataset for a time range from CDAS.

        CDAS subsets the data on the server by variables and time range; cdasws itself waits and
        repeats a request answered with HTTP 429 or 503 and a Retry-After header.

        Args:
            dataset_id: The CDAWeb dataset ID.
            start: The start of the range (inclusive), timezone-aware.
            stop: The end of the range (exclusive), timezone-aware.

        Returns:
            The data as xarray Dataset with the ISTP attributes of each variable, or a result without data
            if the range holds no data or the dataset has no variable to request.

        Raises:
            DataRetrievalError: If the request failed or CDAS answered with an error status.
            DataProcessingError: If CDAS answered successfully but delivered no data.
        """
        variables = self.variables(dataset_id)
        if not variables:
            logger.info("Dataset %s has no variable to request.", dataset_id)
            return FetchResult(None)

        try:
            status, data = self.cdas.get_data(dataset_id, list(variables), start, stop, dataRepresentation=DataRepresentation.XARRAY)
        except (requests.RequestException, OSError) as error:
            raise DataRetrievalError(f"CDAS request for {dataset_id} failed: {error}") from error

        status_code = status.get("http", {}).get("status_code")
        if status_code == CDAS_NO_DATA_STATUS:
            logger.info("CDAS has no data of %s for %s to %s.", dataset_id, start, stop)
            return FetchResult(None)
        if status_code != HTTPStatus.OK:
            raise DataRetrievalError(f"CDAS answered HTTP {status_code} for {dataset_id}: {status.get('cdas')}")
        if not isinstance(data, xr.Dataset):
            raise DataProcessingError(f"CDAS answered HTTP 200 for {dataset_id} but delivered no data: {status.get('cdas')}")
        return FetchResult(data, source_url=CDAS_ENDPOINT)

    def variables(self, dataset_id: str) -> Sequence[str]:
        """Names the variables to request for a dataset.

        Args:
            dataset_id: The CDAWeb dataset ID.

        Returns:
            The variables the resolver names, or else the variables CDAS lists for the dataset, read once per client.

        Raises:
            DataRetrievalError: If the variable list cannot be read from CDAS.
        """
        if self.variables_for is not None:
            resolved = self.variables_for(dataset_id)
            if resolved is not None:
                return resolved
        if dataset_id not in self._listed_variables:
            try:
                self._listed_variables[dataset_id] = self.cdas.get_variable_names(dataset_id)
            except (requests.RequestException, OSError) as error:
                raise DataRetrievalError(f"CDAS variable list of {dataset_id} could not be read: {error}") from error
        return self._listed_variables[dataset_id]
