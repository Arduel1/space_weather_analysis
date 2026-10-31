import logging
from collections.abc import Iterator

from catalog_collector.data_catalog.discovery.discoverers.base import AbstractDiscoverer, DiscoveryYield
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.gfz import GfzClient

logger = logging.getLogger(__name__)


class GfzDiscoverer(AbstractDiscoverer):
    """Lists the GFZ Potsdam indices the GFZ client serves as catalog entries."""

    source_type = SourceType.GFZ_POTSDAM

    def discover(self) -> Iterator[DiscoveryYield]:
        """Yields one catalog entry per index the GFZ client serves; GFZ offers no metadata to store.

        Yields:
            The dataset ID, the catalog entry and no metadata.
        """
        for entry in GfzClient.get_catalog_entries():
            dataset_id = entry.get("dataset_id")
            if not dataset_id:
                logger.warning("GFZ entry without dataset ID skipped: %s", entry)
                continue
            yield dataset_id, entry, None
