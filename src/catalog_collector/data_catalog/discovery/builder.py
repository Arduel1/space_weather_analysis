import json
import logging
import pathlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from hapiclient.util import HAPIError

from catalog_collector.data_catalog.availability_state import mark_missing
from catalog_collector.data_catalog.discovery.discoverers.base import AbstractDiscoverer
from catalog_collector.data_catalog.snapshot import CatalogSnapshotLoader
from catalog_collector.metadata_catalog.snapshot import MetadataSnapshotLoader
from space_weather_analysis.catalog.models import CatalogEntryDict
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.util.error_handling import CatalogError, DataRetrievalError

logger = logging.getLogger(__name__)

# A discovered source is identified by dataset ID, source type and source ID.
type SourceSignature = tuple[str, str, str]


class CatalogBuilder:
    """Builds the master catalog file and repository from the results of several discoverers.

    Discovery never deletes: a source a completed discoverer no longer lists is kept and only counted as
    missing, with the same miss count the availability refreshes keep, and the sources of a failed discoverer
    or failed server stay as they are.
    """

    def __init__(
        self,
        discoverers: Sequence[AbstractDiscoverer],
        catalog_output_path: pathlib.Path,
        catalog_repository: CatalogRepository,
        catalog_loader: CatalogSnapshotLoader,
        metadata_loader: MetadataSnapshotLoader,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Initializes the builder.

        Args:
            discoverers: The discovery strategies to run.
            catalog_output_path: The catalog file to update.
            catalog_repository: The master catalog, whose sources get the miss counts.
            catalog_loader: The loader that writes discovered catalog entries.
            metadata_loader: The loader that writes discovered metadata.
            clock: Returns the current time in UTC.
        """
        self.discoverers = discoverers
        self.catalog_output_path = catalog_output_path
        self.catalog_repository = catalog_repository
        self.catalog_loader = catalog_loader
        self.metadata_loader = metadata_loader
        self.clock = clock
        self.master_catalog: dict[str, list[CatalogEntryDict]] = {}
        self.canonical_ids: dict[str, str] = {}

    def build_catalog(self) -> None:
        """Runs every discoverer, saves the discovered entries and metadata and writes the catalog file once at the end.

        Raises:
            CatalogError: If the existing catalog file cannot be read.
            OSError: If the catalog file cannot be written.
        """
        self._load_catalog_file()
        for discoverer in self.discoverers:
            name = type(discoverer).__name__
            logger.info("Running discoverer %s.", name)
            try:
                found = self._run(discoverer)
            except (DataRetrievalError, HAPIError) as e:
                logger.warning("Discoverer %s failed; the sources of its type stay as they are: %s", name, e)
                continue
            self._count_missing(discoverer, found)
        self._save_catalog_file()

    def _run(self, discoverer: AbstractDiscoverer) -> set[SourceSignature]:
        """Saves every entry and metadata a discoverer yields.

        Args:
            discoverer: The discoverer to run.

        Returns:
            The signatures of the discovered sources.
        """
        found: set[SourceSignature] = set()
        for dataset_id, entry, metadata in discoverer.discover():
            canonical_id = self.canonical_ids.setdefault(dataset_id.lower(), dataset_id)
            found.add((canonical_id, entry["type"], entry["source_id"]))
            if self._update_catalog(canonical_id, entry):
                self.catalog_loader.upsert_dataset(canonical_id, self.master_catalog[canonical_id])
            if metadata:
                self.metadata_loader.upsert_metadata(canonical_id, metadata)
        return found

    def _update_catalog(self, dataset_id: str, entry: CatalogEntryDict) -> bool:
        """Adds or replaces the entry of one source in the in-memory catalog.

        Args:
            dataset_id: The canonical dataset ID.
            entry: The discovered entry.

        Returns:
            True if the catalog changed.
        """
        sources = self.master_catalog.setdefault(dataset_id, [])
        for index, existing in enumerate(sources):
            if existing["type"] == entry["type"] and existing["source_id"] == entry["source_id"]:
                if existing == entry:
                    return False
                sources[index] = entry
                return True
        sources.append(entry)
        return True

    def _count_missing(self, discoverer: AbstractDiscoverer, found: set[SourceSignature]) -> None:
        """Counts one more miss for each stored source of the discoverer's type that it did not list.

        Sources of a server or group the discoverer could not read are left out, as their datasets are unknown.

        Args:
            discoverer: The completed discoverer.
            found: The signatures of the sources it listed.
        """
        checked_at = self.clock()
        for item in self.catalog_repository.get_all():
            changed = False
            for source in item["sources"]:
                if source.get("type") != discoverer.source_type or source.get("source_id") in discoverer.failed_source_ids:
                    continue
                if (item["dataset_id"], source["type"], source["source_id"]) not in found:
                    mark_missing(source, checked_at)
                    changed = True
            if changed:
                self.catalog_repository.save(item)

    def _load_catalog_file(self) -> None:
        """Reads the existing catalog file into memory; a missing file starts an empty catalog.

        Raises:
            CatalogError: If the file exists but is no JSON mapping from dataset ID to sources.
        """
        if not self.catalog_output_path.exists():
            return
        try:
            data = json.loads(self.catalog_output_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise CatalogError(f"Catalog file {self.catalog_output_path} cannot be read: {e}") from e
        if not isinstance(data, dict):
            raise CatalogError(f"Catalog file {self.catalog_output_path} is no mapping from dataset ID to sources.")
        self.master_catalog = data
        self.canonical_ids = {dataset_id.lower(): dataset_id for dataset_id in data}
        logger.info("Loaded %d datasets from the catalog file.", len(self.master_catalog))

    def _save_catalog_file(self) -> None:
        """Writes the in-memory catalog to the catalog file, sorted by dataset ID ignoring case.

        Raises:
            OSError: If the file cannot be written.
        """
        sorted_catalog = dict(sorted(self.master_catalog.items(), key=lambda item: item[0].lower()))
        self.catalog_output_path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_output_path.write_text(json.dumps(sorted_catalog, indent=2), encoding="utf-8")
        logger.info("Catalog file %s written with %d datasets.", self.catalog_output_path, len(sorted_catalog))
