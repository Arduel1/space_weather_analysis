import logging
import sys

from pydantic import ValidationError

from catalog_collector.availability import AvailabilityRefresher
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.config.mongo import MongoSettings
from space_weather_analysis.database.connection import MongoConnection
from space_weather_analysis.logging.config import setup_logging
from space_weather_analysis.metadata.repository import MongoMetadataRepository

logger = logging.getLogger(__name__)


def main() -> None:
    """Refreshes the availability of the HAPI sources in the master catalog from the HAPI metadata mirror.

    Meant for periodic runs between collector runs. An unreachable mirror leaves the catalog unchanged
    and is no failure; invalid settings, a missing database connection or a failed write exit with status 1.
    """
    setup_logging(service_name="AvailabilityRefresh")

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

    try:
        AvailabilityRefresher(MongoCatalogRepository(db), MongoMetadataRepository(db)).refresh()
    except Exception as e:
        logger.critical(f"Availability refresh failed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
