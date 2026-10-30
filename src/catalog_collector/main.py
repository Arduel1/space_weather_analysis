import logging
import pathlib
import sys
from typing import List

from catalog_collector.data_catalog.discovery.builder import CatalogBuilder
from catalog_collector.data_catalog.discovery.discoverers.base import AbstractDiscoverer
from catalog_collector.data_catalog.discovery.discoverers.cdaweb import CdawebDiscoverer
from catalog_collector.data_catalog.discovery.discoverers.gfz import GfzDiscoverer
from catalog_collector.data_catalog.discovery.discoverers.hapi import HapiDiscoverer
from catalog_collector.data_catalog.hapi_availability import AvailabilityRefresher
from catalog_collector.data_catalog.snapshot import CatalogSnapshotLoader
from catalog_collector.metadata_catalog.snapshot import MetadataSnapshotLoader
from space_weather_analysis.database.connection import connect_from_environment
from space_weather_analysis.logging.config import setup_logging

# Setup logging configuration for the service
setup_logging(service_name="CatalogCollector")

logger = logging.getLogger(__name__)

# Service-specific constants defining file system paths
BASE_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
CATALOG_FILE = DATA_DIR / "config" / "master_catalog.json"
METADATA_FILE = DATA_DIR / "config" / "dataset_metadata.json"
HAPI_SERVER_LIST_FILE = DATA_DIR / "hapi_config" / "all.txt"


def main() -> None:
    """Executes the catalog collection workflow.

    This function orchestrates the discovery of datasets from external sources,
    the construction of a unified catalog file, and the synchronization of
    this catalog with the central database.
    """
    logger.info("Starting Catalog Collector Service execution.")

    connection, db = connect_from_environment()

    try:
        # Initialize discovery strategies for different data providers
        # hapi_discoverer = HapiDiscoverer(
        #     server_list_path=HAPI_SERVER_LIST_FILE,
        #     include_test_servers=True,
        # )
        # fido_discoverer = CdawebDiscoverer()
        # gfz_discoverer = GfzDiscoverer()

        # discoverers: List[AbstractDiscoverer] = [hapi_discoverer, fido_discoverer, gfz_discoverer]

        # The catalog is loaded first, as the metadata load reads the HAPI server of each dataset from it
        catalog_loader = CatalogSnapshotLoader(db=db, file_path=CATALOG_FILE)
        metadata_loader = MetadataSnapshotLoader(db=db, file_path=METADATA_FILE)
        catalog_loader.load_to_db()
        metadata_loader.load_metadata_to_db()

        # The snapshot holds the time ranges of its creation date, so the refresh runs after the load
        try:
            AvailabilityRefresher(catalog_loader.catalog_repository, metadata_loader.metadata_repository).refresh()
        except Exception as e:
            logger.error(f"Availability refresh failed; the catalog keeps the snapshot time ranges: {e}", exc_info=True)

        # Aggregate findings from all discoverers into a master JSON file
        # logger.info(f"Targeting output file: {CATALOG_FILE}")
        # builder = CatalogBuilder(
        #     discoverers=discoverers, catalog_output_path=CATALOG_FILE, catalog_loader=catalog_loader, metadata_loader=metadata_loader
        # )
        # builder.build_catalog()

    except Exception as e:
        logger.critical(f"Catalog Collector execution failed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Clean up resources before exiting
        connection.close()

    logger.info("Catalog Collector Service execution finished successfully.")


if __name__ == "__main__":
    main()
