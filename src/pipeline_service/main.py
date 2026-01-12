import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from src.pipeline_service.orchestrator import PipelineOrchestrator
from src.space_weather_analysis.database.connection import DatabaseConnection
from src.space_weather_analysis.logging.config import setup_logging

logger = logging.getLogger(__name__)

HAPI_SOURCE_ID = "HAPI"
FIDO_CDAWEB_SOURCE_ID = "FIDO_CDAWEB"
GFZ_SOURCE_ID = "GFZ_POTSDAM"
LOCAL_SOURCE_ID = "LOCAL"


def main() -> None:
    """
    Initializes and runs the Pipeline Service.

    This entry point sets up the runtime environment, establishes the database
    connection, and initializes the PipelineOrchestrator. It defines a manual
    configuration dictionary for immediate execution needs and subsequently
    triggers the daemon mode to process incoming jobs from the database.
    """
    # Initialize centralized logging for this service
    setup_logging(service_name="PipelineService")
    logger.info("Initializing Pipeline Service...")

    # Establish connection to the database
    db = DatabaseConnection.get_database()
    if db is None:
        logger.critical("Failed to connect to the database. Exiting.")
        sys.exit(1)

    # Define the parameters for a manual download run.
    # This dictionary allows for immediate execution of specific datasets
    # without submitting a job through the database first.
    # It maps source identifiers to their specific query configurations.

    # Configuration for HAPI Data Ingestion
    hapi_manual_config: Dict[str, Any] = {
        "start_time": datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
        "end_time": datetime(2026, 1, 12, 0, 0, 0, tzinfo=timezone.utc),
        "datasets": [
            "goesp_xray_flux_P1M",  # 1-minute X-Ray Flux
            "goesp_part_flux_P5M",  # 5-minute Particle Flux
        ],
    }

    # Configuration for FIDO/CDAWeb Data Ingestion
    fido_manual_config: Dict[str, Any] = {
        "start_time": datetime(2025, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
        "end_time": datetime(2025, 11, 26, 0, 0, 0, tzinfo=timezone.utc),
        "datasets": [],  # Add dataset IDs here to enable FIDO download
    }

    # Configuration for GFZ Data Ingestion
    gfz_manual_config: Dict[str, Any] = {
        "start_time": datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        "end_time": datetime(2026, 1, 12, 0, 0, 0, tzinfo=timezone.utc),
        "datasets": ["Kp"],
    }

    local_manual_config: Dict[str, Any] = {"datasets": ["silso_sunspot_daily", "cme_catalog"]}

    # Aggregate configurations mapped to their source IDs
    manual_jobs: Dict[str, Dict[str, Any]] = {
        HAPI_SOURCE_ID: hapi_manual_config,
        FIDO_CDAWEB_SOURCE_ID: fido_manual_config,
        GFZ_SOURCE_ID: gfz_manual_config,
        LOCAL_SOURCE_ID: local_manual_config,
    }

    try:
        # The orchestrator manages both manual jobs and the database job queue
        orchestrator = PipelineOrchestrator(db=db)

        # Execute Manual Jobs
        # This checks the provided configuration and runs pipelines for any
        # valid datasets defined in the code.
        logger.info("Checking for manual job configurations...")
        orchestrator.run_manual_jobs(manual_jobs)

        # Start Daemon Mode
        # The service enters a continuous loop, polling the database for new
        # user-requested jobs from the frontend.
        logger.info("Starting Pipeline Service Daemon...")
        orchestrator.run_daemon()

    except KeyboardInterrupt:
        logger.info("Pipeline Service stopped by user.")
    except Exception as e:
        logger.critical(f"Pipeline Service encountered a fatal error: {e}", exc_info=True)
        DatabaseConnection.close()
        sys.exit(1)
    finally:
        # Cleanup resources
        DatabaseConnection.close()
        logger.info("Pipeline Service finished successfully.")


if __name__ == "__main__":
    main()
