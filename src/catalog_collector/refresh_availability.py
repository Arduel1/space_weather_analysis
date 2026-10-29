import logging
import sys

from pydantic import ValidationError

from catalog_collector.availability import AvailabilityRefresher
from catalog_collector.metadata_refresh import MetadataRefresher
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.config.mongo import MongoSettings
from space_weather_analysis.database.connection import MongoConnection
from space_weather_analysis.logging.config import setup_logging
from space_weather_analysis.metadata.repository import MongoMetadataRepository

logger = logging.getLogger(__name__)


def main() -> None:
    """Refreshes the HAPI metadata and the availability of the HAPI sources in the master catalog from the HAPI metadata mirror.

    Meant for daily runs between collector runs, as the mirror regenerates its files nightly. The metadata
    refresh runs first, as the availability refresh then corrects the coverage of the rebuilt records.
    Each refresh runs even if the other fails. An unreachable mirror leaves everything unchanged and is
    no failure; invalid settings, a missing database connection or a failed refresh exit with status 1.
    """
    setup_logging(service_name="MirrorRefresh")

    try:
        mongo_settings = MongoSettings()
    except ValidationError as e:
        logger.critical(f"Invalid MongoDB settings, check the MONGO_* environment variables: {format_settings_error(e)}. Exiting.")
        sys.exit(1)

    connection = MongoConnection(mongo_settings)
    db = connection.get_database()
    if db is None:
        logger.critical("Failed to obtain database connection. Exiting.")
        sys.exit(1)

    catalog_repository = MongoCatalogRepository(db)
    metadata_repository = MongoMetadataRepository(db)
    failed = False
    try:
        try:
            MetadataRefresher(catalog_repository, metadata_repository).refresh()
        except Exception as e:
            logger.critical(f"Metadata refresh failed: {e}", exc_info=True)
            failed = True
        try:
            AvailabilityRefresher(catalog_repository, metadata_repository).refresh()
        except Exception as e:
            logger.critical(f"Availability refresh failed: {e}", exc_info=True)
            failed = True
    finally:
        connection.close()
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
