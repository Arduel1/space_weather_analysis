import logging
from typing import Any, Dict, Generator, Optional, Tuple

from src.catalog_collector.discoverers.base import AbstractDiscoverer
from src.space_weather_analysis.clients.gfz import GfzClient
from src.space_weather_analysis.util.types import CatalogEntryDict

logger = logging.getLogger(__name__)


class GfzDiscoverer(AbstractDiscoverer):
    """
    Discovery strategy for GFZ Potsdam datasets.

    This class utilizes the GfzClient to retrieve available indices
    and exposes them as catalog entries for the system.
    """

    def __init__(self):
        """Initializes the discoverer with a GFZ client instance."""
        self.client = GfzClient()

    def discover(self) -> Generator[Tuple[str, CatalogEntryDict, Optional[Dict[str, Any]]], None, None]:
        """
        Retrieves dataset definitions from the GFZ client.

        Yields:
            A tuple containing the dataset ID, the catalog entry dictionary,
            and the metadata dictionary.
        """
        logger.info("Starting discovery for GFZ datasets.")

        try:
            entries = self.client.get_catalog_entries()

            for entry in entries:
                dataset_id = entry.get("dataset_id")
                if not dataset_id:
                    logger.warning(f"Skipping GFZ entry due to missing dataset_id: {entry}")
                    continue

                # Retrieve full metadata for this dataset
                metadata = self.client.get_dataset_metadata(dataset_id)

                yield dataset_id, entry, metadata

        except Exception as e:
            logger.error(f"Error discovering GFZ datasets: {e}", exc_info=True)
