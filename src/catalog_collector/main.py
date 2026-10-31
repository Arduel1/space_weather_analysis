import logging
import pathlib
import sys

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from pymongo.database import Database

from catalog_collector.data_catalog.discovery.builder import CatalogBuilder
from catalog_collector.data_catalog.discovery.discoverers.cdaweb import CdawebDiscoverer
from catalog_collector.data_catalog.discovery.discoverers.gfz import GfzDiscoverer
from catalog_collector.data_catalog.discovery.discoverers.hapi import HapiDiscoverer
from catalog_collector.data_catalog.snapshot import CatalogSnapshotLoader
from catalog_collector.metadata_catalog.snapshot import MetadataSnapshotLoader
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.database.connection import connect_from_environment
from space_weather_analysis.logging.config import setup_logging
from space_weather_analysis.metadata.repository import MongoMetadataRepository

logger = logging.getLogger(__name__)


class CatalogCollectorSettings(BaseSettings):
    """Files and switches of the catalog collector, read from environment variables `CATALOG_COLLECTOR_{field name}` in upper case.

    Attributes:
        data_dir: The directory holding the catalog snapshot, the metadata snapshot and the HAPI server list; a relative
            path is read from the working directory of the process.
        discovery_enabled: Whether the collector queries every provider for its datasets after loading the snapshots;
            a full run takes hours, so it is off unless switched on.
    """

    model_config = SettingsConfigDict(env_prefix="CATALOG_COLLECTOR_", frozen=True)

    data_dir: pathlib.Path = pathlib.Path("data")
    discovery_enabled: bool = False

    @property
    def catalog_file(self) -> pathlib.Path:
        """The master catalog snapshot, which discovery also writes."""
        return self.data_dir / "config" / "master_catalog.json"

    @property
    def metadata_file(self) -> pathlib.Path:
        """The snapshot of HAPI /info responses."""
        return self.data_dir / "config" / "dataset_metadata.json"

    @property
    def hapi_server_list_file(self) -> pathlib.Path:
        """The list of HAPI servers that discovery queries."""
        return self.data_dir / "hapi_config" / "all.txt"


def main() -> None:
    """Loads the catalog and metadata snapshots into the database and, if switched on, runs the dataset discovery.

    Any failure exits with status 1, so services waiting for a successful collector run do not start on a
    catalog that failed to load.
    """
    setup_logging(service_name="CatalogCollector")

    try:
        settings = CatalogCollectorSettings()
    except ValidationError as e:
        logger.critical("Invalid collector settings, check the CATALOG_COLLECTOR_* environment variables: %s. Exiting.", format_settings_error(e))
        sys.exit(1)

    connection, db = connect_from_environment()
    try:
        collect(db, settings)
    except Exception:
        logger.exception("Catalog collector failed. Exiting.")
        sys.exit(1)
    finally:
        connection.close()
    logger.info("Catalog collector finished.")


def collect(db: Database, settings: CatalogCollectorSettings) -> None:
    """Wires the repositories into the loaders, loads both snapshots and runs the discovery if it is switched on.

    Args:
        db: The MongoDB database instance.
        settings: The collector settings.
    """
    catalog_repository = MongoCatalogRepository(db)
    metadata_repository = MongoMetadataRepository(db)
    catalog_loader = CatalogSnapshotLoader(catalog_repository, settings.catalog_file)
    metadata_loader = MetadataSnapshotLoader(catalog_repository, metadata_repository, settings.metadata_file)

    # The catalog is loaded first, as the metadata load reads the HAPI server of each dataset from it
    catalog_loader.load_to_db()
    metadata_loader.load_metadata_to_db()

    if not settings.discovery_enabled:
        logger.info("Dataset discovery is switched off.")
        return
    discoverers = [HapiDiscoverer(settings.hapi_server_list_file, include_test_servers=True), CdawebDiscoverer(), GfzDiscoverer()]
    CatalogBuilder(discoverers, settings.catalog_file, catalog_repository, catalog_loader, metadata_loader).build_catalog()


if __name__ == "__main__":
    main()
