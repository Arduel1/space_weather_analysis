import logging
import pathlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from astropy.table import Row, Table
from parfive.results import Results as ParfiveResults
from requests.exceptions import JSONDecodeError
from sunpy.net import Fido
from sunpy.net import attrs as a
from sunpy.net.cdaweb import attrs as cdaweb_attrs
from sunpy.net.cdaweb.helpers import get_datasets, get_observatory_groups
from sunpy.net.fido_factory import UnifiedResponse

from space_weather_analysis.catalog.models import CatalogEntryDict
from space_weather_analysis.clients.base import BaseClient
from space_weather_analysis.util.resilience import retry_interaction

logger = logging.getLogger(__name__)


class CdawebClient(BaseClient):
    """A unified client for CDAWeb interactions via SunPy.

    This class handles both the discovery of observatory groups and datasets,
    as well as the retrieval of data files using the Fido search and fetch
    mechanisms.
    """

    SOURCE_TYPE = "FIDO_CDAWEB"

    # --- Discovery Methods ---

    @retry_interaction(max_retries=3, exceptions=(Exception,))
    def get_observatory_groups(self) -> Optional[Table]:
        """Fetches all available observatory groups from CDAWeb.

        Returns:
            An astropy Table containing observatory groups,
            or None if the request fails or returns empty results.
        """
        logger.info("Fetching all observatory groups from CDAWeb...")
        try:
            groups = get_observatory_groups()
            if groups is None or len(groups) == 0:
                logger.warning("CDAWeb returned empty observatory groups list.")
                return None

            logger.info(f"Successfully found {len(groups)} groups.")
            return groups
        except Exception as e:
            logger.error(f"Failed to get observatory groups: {e}", exc_info=True)
            return None

    @retry_interaction(max_retries=3, exceptions=(Exception,))
    def get_datasets_for_group(self, group_name: str) -> Optional[Table]:
        """Fetches all datasets associated with a specific observatory group.

        Args:
            group_name: The name of the group to query.

        Returns:
            An astropy Table containing dataset definitions,
            or None if the request fails.
        """
        logger.info(f"Fetching datasets for group '{group_name}'...")
        try:
            datasets = get_datasets(group_name)

            if datasets is None or len(datasets) == 0:
                logger.info(f"No datasets found for group '{group_name}'.")
                return None

            return datasets

        except JSONDecodeError as e:
            response_text = "No response text available"
            if hasattr(e, "response") and e.response is not None:  # type: ignore
                response_text = e.response.text  # type: ignore

            logger.warning(f"Failed for group '{group_name}'. API returned non-JSON response. Error: {e}. Response Text: {response_text}")
            return None
        except Exception as e:
            logger.error(
                f"Failed to get datasets for group '{group_name}': {e}",
                exc_info=True,
            )
            return None

    def parse_dataset_row(self, row: Union[Row, Dict[str, Any]], group_name: str) -> Optional[CatalogEntryDict]:
        """Parses a raw dataset row from SunPy into a standardized catalog entry.

        Args:
            row: The raw row data.
            group_name: The group name associated with this row.

        Returns:
            A dictionary conforming to the catalog entry structure,
            or None if required fields are missing.
        """
        try:
            dataset_id = str(row["Id"])
            start_date = row["Start"]
            stop_date = row["End"]

            if not start_date or not stop_date:
                logger.warning(f"Dataset {dataset_id} (Group {group_name}) has no time range. Skipping.")
                return None

            return {
                "type": self.SOURCE_TYPE,
                "dataset_id": dataset_id,
                "source_id": group_name,
                "time_range": {
                    "start_time": str(start_date),
                    "end_time": str(stop_date),
                },
            }
        except KeyError as e:
            logger.error(
                f"Row for group {group_name} is malformed. Missing key: {e}. Row: {row}",
                exc_info=True,
            )
            return None
        except Exception as e:
            logger.error(f"Failed to process dataset row for {group_name}: {e}", exc_info=True)
            return None

    # --- BaseClient Interface Implementation ---

    def get_catalog_entries(self) -> List[Dict[str, Any]]:
        """Lists available observatory groups as standardized catalog entries.

        Returns:
            A list of dictionaries representing groups.
        """
        table = self.get_observatory_groups()
        if table:
            return [dict(row) for row in table]
        return []

    def get_dataset_metadata(self, dataset_id: str) -> Dict[str, Any]:
        """Provides metadata for a specific dataset.

        Args:
            dataset_id: The dataset identifier.

        Returns:
            A dict containing the ID and source type.
        """
        return {"id": dataset_id, "source": self.SOURCE_TYPE}

    def get_data(self, dataset_id: str, start_time: datetime, end_time: datetime, **kwargs: Any) -> List[pathlib.Path]:
        """Searches Fido for a dataset within a time range and downloads files.

        Args:
            dataset_id: The dataset ID to search for.
            start_time: The start of the time range.
            end_time: The end of the time range.
            **kwargs: Must contain 'download_path' (pathlib.Path or str).

        Returns:
            A list of downloaded file paths.
        """
        download_path = kwargs.get("download_path")
        if not download_path:
            logger.error("Download path is required for CdawebClient.get_data")
            return []

        destination = pathlib.Path(download_path)

        logger.info(f"Searching Fido for '{dataset_id}' from {start_time} to {end_time}...")

        try:
            time_query = a.Time(start_time, end_time)
            dataset_query = cdaweb_attrs.Dataset(dataset_id)

            query_response: UnifiedResponse = Fido.search(time_query, dataset_query)

            if not query_response:
                logger.warning(f"Fido.search returned 0 results for '{dataset_id}'.")
                return []

            logger.info(f"Fido found results. Downloading files to: {destination}")

            file_results: ParfiveResults = Fido.fetch(
                query_response,
                path=str(destination / "{file}"),
                progress=False,
            )

            downloaded_paths = [pathlib.Path(fp) for fp in file_results.data]

            if not downloaded_paths:
                logger.warning("Fido reported results, but no files were downloaded.")
                return []

            logger.info(f"Download complete. {len(downloaded_paths)} files fetched.")
            return downloaded_paths

        except Exception as e:
            logger.error(f"Fido search/fetch failed for {dataset_id}: {e}", exc_info=True)
            return []
