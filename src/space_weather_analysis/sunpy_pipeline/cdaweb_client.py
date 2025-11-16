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
    and datasets using public sunpy helpers.
    Metadata is no longer fetched here, but from the files themselves.
    """

    def get_observatory_groups(self) -> Optional[Table]:
        """
        Fetches all available observatory groups from CDAWeb.

        Uses the sunpy.net.cdaweb.helpers module.

        Returns:
            Optional[Table]: An astropy Table of observatory groups,
                             or None on failure.
        """
        logger.info("Fetching all observatory groups...")
        try:
            groups: Table = get_observatory_groups()
            logger.info(f"Successfully found {len(groups)} groups.")
            return groups
        except Exception as e:
            logger.error(f"Failed to get observatory groups: {e}", exc_info=True)
            return None

    def get_datasets_for_group(self, group_name: str) -> Optional[Table]:
        """
        Fetches all datasets for a specific observatory group.

        Uses the sunpy.net.cdaweb.helpers module.

        Args:
            group_name (str): The name of the group to query.

        Returns:
            Optional[Table]: An astropy Table of datasets, or None on failure.
        """
        logger.info(f"Fetching datasets for group '{group_name}'...")
        try:
            datasets: Table = get_datasets(group_name)
            logger.info(f"Found {len(datasets)} datasets for '{group_name}'.")
            return datasets
        except JSONDecodeError as e:
            response_text = "No response text available"
            if hasattr(e, "response") and e.response is not None:
                response_text = e.response.text
            logger.warning(
                f"Failed for group '{group_name}'. API returned non-JSON response. "
                f"Error: {e}. Response Text (first 100 chars): "
                f"{response_text[:100]}"
            )
            return None
        except Exception as e:
            logger.error(
                f"Failed to get datasets for group '{group_name}': {e}",
                exc_info=True,
            )
            return None
