import json
import logging
import pathlib
from typing import Any

from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.util.error_handling import CatalogError

logger = logging.getLogger(__name__)

# Source fields the availability refreshes and discovery runs maintain; a snapshot reload keeps them for sources that have them.
AVAILABILITY_FIELDS = ("time_range", "ongoing", "availability_checked_at", "missing_runs", "missing_since")


class CatalogSnapshotLoader:
    """Loads the master catalog file into the catalog repository.

    The file maps each dataset ID to its sources; the repository then reflects the file, while the
    availability state the refreshes found since is kept.
    """

    def __init__(self, catalog_repository: CatalogRepository, file_path: pathlib.Path) -> None:
        """Initializes the loader.

        Args:
            catalog_repository: The master catalog to load into.
            file_path: Path to the catalog JSON file.
        """
        self.catalog_repository = catalog_repository
        self.file_path = file_path

    def load_to_db(self) -> None:
        """Saves every dataset of the catalog file, keeping the availability state of stored sources.

        Raises:
            CatalogError: If the file is missing, no JSON or no mapping from dataset ID to sources.
            pymongo.errors.PyMongoError: If the catalog cannot be written.
        """
        items = self._read_file()
        logger.info("Loading %d datasets from catalog file %s.", len(items), self.file_path)
        for item in items:
            item["sources"].sort(key=source_sort_key)
        keep_availability_state(items, self.catalog_repository.get_all())
        self.catalog_repository.save_many(items)

    def _read_file(self) -> list[MasterCatalogItem]:
        """Reads and parses the catalog file.

        Returns:
            One catalog item per dataset of the file.

        Raises:
            CatalogError: If the file is missing, no JSON or no mapping from dataset ID to sources.
        """
        try:
            data: Any = json.loads(self.file_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise CatalogError(f"Catalog file {self.file_path} cannot be read: {e}") from e
        if not isinstance(data, dict) or not all(isinstance(sources, list) for sources in data.values()):
            raise CatalogError(f"Catalog file {self.file_path} is no mapping from dataset ID to a list of sources.")
        return [{"dataset_id": dataset_id, "sources": sources} for dataset_id, sources in data.items()]

    def upsert_dataset(self, dataset_id: str, sources: list[CatalogEntryDict]) -> None:
        """Saves one discovered dataset with its sources in a fixed order, keeping their availability state.

        The sources were just discovered, so a miss count they had is reset.

        Args:
            dataset_id: The dataset ID.
            sources: The discovered sources of the dataset.

        Raises:
            pymongo.errors.PyMongoError: If the dataset cannot be written.
        """
        item: MasterCatalogItem = {"dataset_id": dataset_id, "sources": sorted((source.copy() for source in sources), key=source_sort_key)}
        stored_item = self.catalog_repository.get(dataset_id)
        keep_availability_state([item], [stored_item] if stored_item else [])
        for source in item["sources"]:
            if "missing_runs" in source:
                source["missing_runs"] = 0
            source.pop("missing_since", None)
        self.catalog_repository.save(item)


def keep_availability_state(items: list[MasterCatalogItem], stored_items: list[MasterCatalogItem]) -> None:
    """Carries the availability state of stored sources over into new catalog items.

    The catalog file holds the time ranges of its creation; a stored source that a refresh or discovery run has
    updated keeps its newer time range, ongoing state and miss count, so a collector restart with an unreachable
    provider does not fall back to stale ranges.

    Args:
        items: The new items, changed in place.
        stored_items: The stored items of the same datasets.
    """
    stored_sources: dict[tuple[str, str, str], CatalogEntryDict] = {}
    for stored_item in stored_items:
        for stored_source in stored_item["sources"]:
            if "availability_checked_at" in stored_source or "missing_runs" in stored_source:
                stored_sources[(stored_item["dataset_id"], stored_source.get("type", ""), stored_source.get("source_id", ""))] = stored_source

    for item in items:
        for source in item["sources"]:
            refreshed_source = stored_sources.get((item["dataset_id"], source.get("type", ""), source.get("source_id", "")))
            if refreshed_source is None:
                continue
            for field in AVAILABILITY_FIELDS:
                if field in refreshed_source:
                    # TypedDict keys held in a variable are not checked by the type checker
                    source[field] = refreshed_source[field]  # type: ignore[literal-required]


def source_sort_key(source: CatalogEntryDict) -> tuple[Any, ...]:
    """Orders the sources of a dataset by type, source ID and time range, so stored source lists are deterministic.

    Args:
        source: A catalog source.

    Returns:
        The sort key.
    """
    time_range = source.get("time_range", {})
    return (source.get("type"), source.get("source_id"), time_range.get("start_time"), time_range.get("end_time"))
