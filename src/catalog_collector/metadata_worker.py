import logging
import sys

from pydantic import ValidationError

from catalog_collector.data_catalog.tasks import hapi_availability_kind
from catalog_collector.metadata_catalog.tasks import hapi_metadata_kind
from catalog_collector.scheduling.repository import MongoMetadataTaskRepository
from catalog_collector.scheduling.worker import MetadataWorker, WorkerSettings
from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.database.connection import connect_from_environment
from space_weather_analysis.logging.config import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Runs the metadata worker as a long-lived background process.

    Invalid settings or a missing database connection exit with status 1; failing tasks are retried and never end the process.
    """
    setup_logging(service_name="MetadataWorker")

    try:
        worker_settings = WorkerSettings()
    except ValidationError as e:
        logger.critical(f"Invalid worker settings, check the METADATA_WORKER_* environment variables: {format_settings_error(e)}. Exiting.")
        sys.exit(1)

    connection, db = connect_from_environment()
    kinds = [hapi_metadata_kind(db, worker_settings), hapi_availability_kind(db, worker_settings)]
    try:
        MetadataWorker(MongoMetadataTaskRepository(db), kinds, worker_settings).run_forever()
    finally:
        connection.close()


if __name__ == "__main__":
    main()
