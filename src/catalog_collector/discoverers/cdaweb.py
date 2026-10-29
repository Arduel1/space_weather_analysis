import logging
from typing import Generator

from catalog_collector.discoverers.base import AbstractDiscoverer, DiscoveryYield
from space_weather_analysis.clients.cdaweb import CdawebClient

logger = logging.getLogger(__name__)


class CdawebDiscoverer(AbstractDiscoverer):
    """Discovers datasets by querying the data source via the CdawebClient.

    This class orchestrates the discovery process by fetching all observatory
    groups and then retrieving the dataset list for each group.
    """

    def __init__(self) -> None:
        """Initializes the discoverer with a shared CdawebClient."""
        self.client = CdawebClient()

    def _discover_datasets_for_group(self, group_name: str) -> Generator[DiscoveryYield, None, None]:
        """Retrieves and iterates over all datasets for a single group.

        Args:
            group_name (str): The name of the group to query.

        Yields:
            A generator yielding (dataset_id, entry, metadata) for the group.
        """
        logger.info(f"Getting datasets for group: {group_name}")
        try:
            datasets_table = self.client.get_datasets_for_group(group_name)

            if datasets_table is None:
                return

            for dataset_row in datasets_table:
                # Use the client's helper to parse the raw row into a standardized entry
                entry = self.client.parse_dataset_row(dataset_row, group_name)
                # parse_dataset_row sets dataset_id on every entry it returns; the check lets type checkers see that.
                if entry and "dataset_id" in entry:
                    # Metadata retrieval for CDAWeb is handled in the Data Pipeline or separate process
                    yield str(entry["dataset_id"]), entry, None

        except Exception as e:
            logger.error(
                f"Failed to get or process datasets for group {group_name}: {e}",
                exc_info=True,
            )

    def discover(self) -> Generator[DiscoveryYield, None, None]:
        """Implements the discovery process for data sources.

        Iterates through all observatory groups, queries each for its
        datasets, and yields standardized entries.
        """
        logger.info("--- Starting CDAWeb Source Discovery ---")
        try:
            all_groups = self.client.get_observatory_groups()
            if not all_groups:
                logger.error("Could not get observatory groups.")
                return

            logger.info(f"Found {len(all_groups)} groups.")

            for group_row in all_groups:
                try:
                    group_name = str(group_row["Group"])
                    yield from self._discover_datasets_for_group(group_name)
                except KeyError as e:
                    logger.warning(f"Malformed group row, missing 'Group' key: {group_row}. Error: {e}")
                except Exception as e:
                    logger.warning(
                        f"Failed to process group row {group_row}: {e}",
                        exc_info=True,
                    )

        except Exception as e:
            logger.error(f"CDAWeb source discovery failed: {e}", exc_info=True)
        logger.info("--- CDAWeb Source Discovery Finished ---")
