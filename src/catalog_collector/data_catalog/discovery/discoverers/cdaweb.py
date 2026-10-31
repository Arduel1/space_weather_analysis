import logging
from collections.abc import Iterator

from catalog_collector.data_catalog.discovery.discoverers.base import AbstractDiscoverer, DiscoveryYield
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.cdaweb import CdawebClient
from space_weather_analysis.util.error_handling import DataRetrievalError

logger = logging.getLogger(__name__)


class CdawebDiscoverer(AbstractDiscoverer):
    """Lists the CDAWeb datasets of every observatory group as catalog entries.

    The metadata of CDAWeb datasets comes from the CDAS dataset list, the master files and SPASE, so discovery yields none.
    """

    source_type = SourceType.CDAWEB

    def __init__(self, client: CdawebClient | None = None) -> None:
        """Initializes the discoverer.

        Args:
            client: The CDAWeb client; a new one if None.
        """
        super().__init__()
        self.client = client or CdawebClient()

    def discover(self) -> Iterator[DiscoveryYield]:
        """Yields the datasets of every observatory group; a group that cannot be read is recorded as failed source.

        Yields:
            The dataset ID, the catalog entry and no metadata.

        Raises:
            DataRetrievalError: If the observatory groups cannot be read.
        """
        groups = self.client.get_observatory_groups()
        if not groups:
            raise DataRetrievalError("CDAWeb observatory groups could not be read")
        logger.info("CDAWeb discovery: %d observatory groups.", len(groups))
        for group_row in groups:
            group_name = str(group_row["Group"])
            datasets = self.client.get_datasets_for_group(group_name)
            # The client answers None both for a failed request and for a group without datasets, so the group counts as failed
            if datasets is None:
                self.failed_source_ids.add(group_name)
                continue
            for dataset_row in datasets:
                entry = self.client.parse_dataset_row(dataset_row, group_name)
                if entry and "dataset_id" in entry:
                    yield str(entry["dataset_id"]), entry, None
