import logging
from typing import Optional

from astropy.table import Table
from requests.exceptions import JSONDecodeError
from sunpy.net.cdaweb.helpers import get_datasets, get_observatory_groups

logger = logging.getLogger(__name__)


class CdawebClient:
    """
    A client for interacting with the CDAWeb API.

    This class provides methods to fetch observatory groups
    and datasets using sunpy helper functions. It acts as an
    abstraction layer to handle specific API quirks and error logging.
    """

    def get_observatory_groups(self) -> Optional[Table]:
        """
        Fetches all available observatory groups from CDAWeb.

        Returns:
            Optional[Table]: An astropy Table containing observatory groups,
                             or None if the request fails.
        """
        logger.info("Fetching all observatory groups...")
        try:
            groups: Table = get_observatory_groups()
            if groups is None or len(groups) == 0:
                logger.warning("CDAWeb returned empty observatory groups list.")
                return None

            logger.info(f"Successfully found {len(groups)} groups.")
            return groups
        except Exception as e:
            logger.error(f"Failed to get observatory groups: {e}", exc_info=True)
            return None

    def get_datasets_for_group(self, group_name: str) -> Optional[Table]:
        """
        Fetches all datasets for a specific observatory group.

        Args:
            group_name (str): The name of the group to query (e.g., 'SOHO').

        Returns:
            Optional[Table]: An astropy Table containing dataset definitions,
                             or None if the request fails.
        """
        logger.info(f"Fetching datasets for group '{group_name}'...")
        try:
            datasets: Table = get_datasets(group_name)

            if datasets is None or len(datasets) == 0:
                logger.info(f"No datasets found for group '{group_name}'.")
                return None

            logger.info(f"Found {len(datasets)} datasets for '{group_name}'.")
            return datasets

        except JSONDecodeError as e:
            # Handle cases where the API returns non-JSON (e.g., HTML error pages)
            response_text = "No response text available"
            # Accessing the response object safely if attached to the exception
            if hasattr(e, "response") and e.response is not None:  # type: ignore
                response_text = e.response.text  # type: ignore

            logger.warning(
                f"Failed for group '{group_name}'. API returned non-JSON response. "
                f"Error: {e}. Response Text: {response_text}"
            )
            return None
        except Exception as e:
            logger.error(
                f"Failed to get datasets for group '{group_name}': {e}",
                exc_info=True,
            )
            return None
