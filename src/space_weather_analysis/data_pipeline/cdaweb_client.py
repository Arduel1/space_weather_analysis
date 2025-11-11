import logging
from typing import Any, Dict, List, Optional

from astropy.table import Table
from hapiclient import hapi
from requests.exceptions import JSONDecodeError
from sunpy.net.cdaweb.helpers import get_datasets, get_observatory_groups

logger = logging.getLogger(__name__)

# HAPI Server URL aus der Demo
HAPI_SERVER = "https://cdaweb.gsfc.nasa.gov/hapi"


class CdawebClient:
    """
    A client for interacting with the CDAWeb API.

    This class provides methods to fetch observatory groups,
    datasets, and variable metadata. It uses public sunpy helpers
    for group/dataset discovery and the public HAPI client
    for variable metadata.
    """

    def get_observatory_groups(self) -> Optional[Table]:
        """
        Fetches all available observatory groups from CDAWeb
        using the sunpy.net.cdaweb.helpers module.

        Returns:
            Optional[Table]: A table of observatory groups, or None on failure.
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
        Fetches all datasets for a specific observatory group
        using the sunpy.net.cdaweb.helpers module.

        Args:
            group_name (str): The name of the group to query.

        Returns:
            Optional[Table]: A table of datasets, or None on failure.
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
                f"Error: {e}. Response Text (first 100 chars): {response_text[:100]}"
            )
            return None
        except Exception as e:
            logger.error(f"Failed to get datasets for group '{group_name}': {e}", exc_info=True)
            return None

    def get_variables_for_dataset(self, target_dataset: str) -> List[Dict[str, Any]]:
        """
        Fetches variable metadata for a specific dataset ID
        using the public CDAWeb HAPI client.

        This avoids using private sunpy variables and manual requests.

        Args:
            target_dataset (str): The ID of the dataset (e.g., "WI_H1_SWE_RTN").

        Returns:
            List[Dict[str, Any]]: A list of dictionaries, each containing
                                  metadata for a single variable.
                                  Returns empty list on failure.
        """
        logger.info(f"Fetching variables for dataset '{target_dataset}' using hapiclient...")

        try:
            # Use hapiclient to fetch dataset metadata (info)
            # wie in hapi_demo.py gezeigt
            meta = hapi(HAPI_SERVER, target_dataset)

            hapi_params = meta.get("parameters")

            # Normalize HAPI response to the format expected by the pipeline
            variables_metadata = []
            if hapi_params:
                for param in hapi_params:
                    # Pipeline expects 'Name' and 'LongDescription'
                    # HAPI provides 'name' and 'description'
                    variables_metadata.append(
                        {
                            "Name": param.get("name"),
                            "LongDescription": param.get("description"),
                            "Units": param.get("units"),
                            "Fill": param.get("fill"),
                            "Type": param.get("type"),
                        }
                    )
                logger.info(f"Successfully found {len(variables_metadata)} variables for '{target_dataset}'.")
                return variables_metadata
            else:
                logger.warning(f"HAPI client returned no variables for '{target_dataset}'.")
                return []

        except Exception as e:
            # hapiclient can raise various exceptions
            logger.error(
                f"Failed to get variables for {target_dataset} using hapiclient: {e}",
                exc_info=True,
            )

        return []
