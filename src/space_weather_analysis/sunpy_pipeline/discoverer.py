import logging
from typing import Any, Dict, Generator, Tuple

from space_weather_analysis.sunpy_pipeline.cdaweb_client import CdawebClient
from space_weather_analysis.util.catalog.abstract_discoverer import AbstractDiscoverer

logger = logging.getLogger(__name__)


class FidoCdawebDiscoverer(AbstractDiscoverer):
    """
    Discovers datasets by querying the SunPy/CDAWeb client.
    """

    def __init__(self):
        """
        Initializes the FidoCdawebDiscoverer.
        """
        logger.info("Initialized FidoCdawebDiscoverer.")

    def discover(self) -> Generator[Tuple[str, Dict[str, Any]], None, None]:
        """Iterate all Fido/CDAWeb datasets and yield them."""
        logger.info("--- Starting FIDO_CDAWEB Source Discovery ---")
        try:
            client = CdawebClient()

            all_groups = client.get_observatory_groups()
            if not all_groups:
                logger.error("FIDO_CDAWEB: Could not get observatory groups.")
                return

            logger.info(f"FIDO_CDAWEB: Found {len(all_groups)} groups.")

            for group_row in all_groups:
                group_name = str(group_row["Group"])
                logger.info(f"FIDO_CDAWEB: Getting datasets for group: {group_name}")

                datasets_table = client.get_datasets_for_group(group_name)
                if datasets_table is None or len(datasets_table) == 0:
                    logger.warning(f"FIDO_CDAWEB: No datasets found for group {group_name}.")
                    continue

                for dataset_row in datasets_table:
                    try:
                        dataset_id = str(dataset_row["Id"])
                        start_date = dataset_row["Start"]
                        stop_date = dataset_row["End"]

                        if start_date and stop_date:
                            new_entry = {
                                "type": "FIDO_CDAWEB",
                                "source_id": group_name,
                                "time_range": {
                                    "start_time": start_date,
                                    "end_time": stop_date,
                                },
                            }
                            yield dataset_id, new_entry
                        else:
                            logger.warning(f"Dataset {dataset_id} (Group {group_name}) has no time range. Skipping.")

                    except KeyError as e:
                        logger.error(
                            f"FIDO_CDAWEB: Row for group {group_name} is malformed. "
                            f"Missing key: {e}. Row: {dataset_row}",
                            exc_info=True,
                        )
                    except Exception as e:
                        logger.error(
                            f"FIDO_CDAWEB: Failed to process dataset row {dataset_row}: {e}",
                            exc_info=True,
                        )

        except Exception as e:
            logger.error(f"FIDO_CDAWEB source discovery failed: {e}", exc_info=True)
        logger.info("--- FIDO_CDAWEB Source Discovery Finished ---")
