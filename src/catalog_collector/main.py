import logging
import pathlib
import sys
from typing import List

from pydantic import ValidationError

from catalog_collector.builder import CatalogBuilder
from catalog_collector.discoverers.base import AbstractDiscoverer
from catalog_collector.discoverers.cdaweb import CdawebDiscoverer
from catalog_collector.discoverers.gfz import GfzDiscoverer
from catalog_collector.discoverers.hapi import HapiDiscoverer
from catalog_collector.loader import CatalogLoader
from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.config.mongo import MongoSettings
from space_weather_analysis.database.connection import MongoConnection
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

    # Build the MongoDB settings once from the environment
    try:
        mongo_settings = MongoSettings()
    except ValidationError as e:
        logger.critical(f"Invalid MongoDB settings, check the MONGO_* environment variables: {format_settings_error(e)}. Exiting.")
        sys.exit(1)

    # Establish the database connection for this run
    connection = MongoConnection(mongo_settings)
    db = connection.get_database()
    if db is None:
        logger.critical("Failed to obtain database connection. Exiting.")
        sys.exit(1)

    try:
        # Initialize discovery strategies for different data providers
        # hapi_discoverer = HapiDiscoverer(
        #     server_list_path=HAPI_SERVER_LIST_FILE,
        #     include_test_servers=True,
        # )
        # fido_discoverer = CdawebDiscoverer()
        # gfz_discoverer = GfzDiscoverer()

        # discoverers: List[AbstractDiscoverer] = [hapi_discoverer, fido_discoverer, gfz_discoverer]

        # Initialize Loader first
        loader = CatalogLoader(db=db, file_path=CATALOG_FILE, metadata_path=METADATA_FILE)
        loader.load_to_db()
        loader.load_metadata_to_db()

        # Aggregate findings from all discoverers into a master JSON file
        # logger.info(f"Targeting output file: {CATALOG_FILE}")
        # builder = CatalogBuilder(discoverers=discoverers, catalog_output_path=CATALOG_FILE, loader=loader)
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
