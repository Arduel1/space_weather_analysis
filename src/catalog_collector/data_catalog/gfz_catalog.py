import logging
from datetime import datetime

from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem, SourceType
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.clients.gfz import GfzClient
from space_weather_analysis.util.time_utils import ensure_aware

logger = logging.getLogger(__name__)

# The time format of the catalog time ranges of the GFZ sources.
CATALOG_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def ensure_gfz_sources(catalog_repository: CatalogRepository, now: datetime) -> int:
    """Adds the GFZ indices the GFZ client describes to the catalog, as no provider listing names them.

    A dataset that already has a GFZ source keeps it unchanged.

    Args:
        catalog_repository: The master catalog.
        now: The current time in UTC, the end of the new time ranges.

    Returns:
        The number of added sources.
    """
    changed_items: list[MasterCatalogItem] = []
    for entry in GfzClient.get_catalog_entries():
        dataset_id = entry["dataset_id"]
        if catalog_repository.get_sources(dataset_id, SourceType.GFZ_POTSDAM):
            continue
        start_time = ensure_aware(datetime.fromisoformat(entry["time_range"]["start_time"]))
        source: CatalogEntryDict = {
            "type": SourceType.GFZ_POTSDAM,
            "source_id": entry["source_id"],
            "time_range": {"start_time": start_time.strftime(CATALOG_TIME_FORMAT), "end_time": now.strftime(CATALOG_TIME_FORMAT)},
            "ongoing": True,
        }
        item = catalog_repository.get(dataset_id) or {"dataset_id": dataset_id, "sources": []}
        item["sources"].append(source)
        changed_items.append(item)
    catalog_repository.save_many(changed_items)
    logger.info("GFZ catalog sources: %d added.", len(changed_items))
    return len(changed_items)
