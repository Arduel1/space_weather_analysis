import logging
from typing import Any, Generator

from space_weather_analysis.clients.cdaweb import CdawebClient
from space_weather_analysis.config import FIDO_CDAWEB_SOURCE_ID
from space_weather_analysis.discovery.discoverers.base import AbstractDiscoverer
from space_weather_analysis.util.types import CatalogEntryDict, DiscoveryYield

logger = logging.getLogger(__name__)


class CdawebDiscoverer(AbstractDiscoverer):
    """
    Discovers datasets by querying the data source via the CdawebClient.
    """

    def _get_entry_for_dataset(self, group_name: str, dataset_row: Any) -> Generator[DiscoveryYield, None, None]:
        """
        Parses a single dataset row and yields a standardized catalog entry.

        Handles row-level errors, such as missing keys.

        Args:
            group_name (str): The name of the group this dataset belongs to.
            dataset_row (Any): The raw dataset row object from the client.

        Yields:
            Generator[DiscoveryYield, None, None]:
            A generator yielding a single (dataset_id, entry) tuple.
        """
        try:
            dataset_id = str(dataset_row["Id"])
            start_date = dataset_row["Start"]
            stop_date = dataset_row["End"]

            if start_date and stop_date:
                # Use TypedDict to ensure structure matches CatalogEntryDict definition
                new_entry: CatalogEntryDict = {
                    "type": FIDO_CDAWEB_SOURCE_ID,
                    "source_id": group_name,
                    "time_range": {
                        # Explicit string conversion ensures compatibility with TimeRangeDict
                        "start_time": str(start_date),
                        "end_time": str(stop_date),
                    },
                }
                yield dataset_id, new_entry
            else:
                logger.warning(f"Dataset {dataset_id} (Group {group_name}) has no time range. Skipping.")

        except KeyError as e:
            logger.error(
                f"{FIDO_CDAWEB_SOURCE_ID}: Row for group {group_name} is malformed."
                f"Missing key: {e}. Row: {dataset_row}",
                exc_info=True,
            )
        except Exception as e:
            logger.error(
                f"{FIDO_CDAWEB_SOURCE_ID}: Failed to process dataset row {dataset_row}: {e}",
                exc_info=True,
            )

    def _discover_datasets_for_group(
        self, client: CdawebClient, group_name: str
    ) -> Generator[DiscoveryYield, None, None]:
        """
        Retrieves and iterates over all datasets for a single group.

        Delegates the processing of each dataset row to a helper method.

        Args:
            client (CdawebClient): The client instance to use for queries.
            group_name (str): The name of the group to query.

        Yields:
            Generator[DiscoveryYield, None, None]:
            A generator yielding catalog entries for the group.
        """
        logger.info(f"{FIDO_CDAWEB_SOURCE_ID}: Getting datasets for group: {group_name}")
        try:
            datasets_table = client.get_datasets_for_group(group_name)

            if datasets_table is None or len(datasets_table) == 0:
                logger.warning(f"{FIDO_CDAWEB_SOURCE_ID}: No datasets found for group {group_name}.")
                return

            for dataset_row in datasets_table:
                yield from self._get_entry_for_dataset(group_name, dataset_row)
        except Exception as e:
            # Handle errors during the dataset query for this group
            logger.error(
                f"{FIDO_CDAWEB_SOURCE_ID}: Failed to get or process datasets for group {group_name}: {e}",
                exc_info=True,
            )

    def discover(self) -> Generator[DiscoveryYield, None, None]:
        """
        Implements the discovery process for data sources.

        Iterates through all observatory groups, queries each for its
        datasets, and yields standardized entries.
        """
        logger.info(f"--- Starting {FIDO_CDAWEB_SOURCE_ID} Source Discovery ---")
        try:
            client = CdawebClient()

            all_groups = client.get_observatory_groups()
            if not all_groups:
                logger.error(f"{FIDO_CDAWEB_SOURCE_ID}: Could not get observatory groups.")
                return

            logger.info(f"{FIDO_CDAWEB_SOURCE_ID}: Found {len(all_groups)} groups.")

            for group_row in all_groups:
                try:
                    group_name = str(group_row["Group"])
                    yield from self._discover_datasets_for_group(client, group_name)
                except KeyError as e:
                    logger.warning(
                        f"{FIDO_CDAWEB_SOURCE_ID}: Malformed group row, missing 'Group' key: {group_row}. Error: {e}"
                    )
                except Exception as e:
                    logger.warning(
                        f"{FIDO_CDAWEB_SOURCE_ID}: Failed to process group row {group_row}: {e}",
                        exc_info=True,
                    )

        except Exception as e:
            logger.error(f"{FIDO_CDAWEB_SOURCE_ID} source discovery failed: {e}", exc_info=True)
        logger.info(f"--- {FIDO_CDAWEB_SOURCE_ID} Source Discovery Finished ---")
