import logging
import sys
from datetime import UTC, datetime

from pydantic import ValidationError

from catalog_collector.data_catalog.gfz_catalog import ensure_gfz_sources
from catalog_collector.hapi_mirror.servers import read_server_list
from catalog_collector.scheduling.repository import MongoMetadataTaskRepository
from catalog_collector.scheduling.worker import MetadataWorker, WorkerSettings
from catalog_collector.worker_tasks.cdas_dataset_list import cdas_dataset_list_kind
from catalog_collector.worker_tasks.data_catalog import hapi_availability_kind
from catalog_collector.worker_tasks.metadata_catalog import cdaweb_master_kind, hapi_metadata_kind, spase_record_kind
from catalog_collector.worker_tasks.settings import CatalogSettings, TaskIntervalSettings
from space_weather_analysis.catalog.repository import MongoCatalogRepository
from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.database.connection import connect_from_environment
from space_weather_analysis.logging.config import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Runs the metadata worker as a long-lived background process.

    Invalid settings, an unreadable HAPI server list or a missing database connection exit with status 1; failing tasks
    are retried and never end the process.
    """
    setup_logging(service_name="MetadataWorker")

    try:
        worker_settings = WorkerSettings()
        interval_settings = TaskIntervalSettings()
        catalog_settings = CatalogSettings()
    except ValidationError as e:
        logger.critical("Invalid worker settings, check the METADATA_WORKER_* environment variables: %s. Exiting.", format_settings_error(e))
        sys.exit(1)
    try:
        hapi_server_urls = read_server_list(catalog_settings.hapi_server_list_file)
    except OSError as e:
        logger.critical("HAPI server list %s cannot be read: %s. Exiting.", catalog_settings.hapi_server_list_file, e)
        sys.exit(1)

    connection, db = connect_from_environment()
    ensure_gfz_sources(MongoCatalogRepository(db), datetime.now(UTC))
    kinds = [
        hapi_metadata_kind(db, interval_settings),
        hapi_availability_kind(db, interval_settings, hapi_server_urls),
        cdas_dataset_list_kind(db, interval_settings),
        cdaweb_master_kind(db, interval_settings),
        spase_record_kind(db, interval_settings),
    ]
    try:
        MetadataWorker(MongoMetadataTaskRepository(db), kinds, worker_settings).run_forever()
    finally:
        connection.close()


if __name__ == "__main__":
    main()
