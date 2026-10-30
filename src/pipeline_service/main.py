import logging
import sys
from datetime import UTC, datetime
from typing import Any

from pipeline_service.orchestrator import PipelineOrchestrator
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.database.connection import connect_from_environment
from space_weather_analysis.logging.config import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """Initializes and runs the Pipeline Service.

    This entry point sets up the runtime environment, establishes the database
    connection, and initializes the PipelineOrchestrator. It defines a manual
    configuration dictionary for immediate execution needs and subsequently
    triggers the daemon mode to process incoming jobs from the database.
    """
    # Initialize centralized logging for this service
    setup_logging(service_name="PipelineService")
    logger.info("Initializing Pipeline Service...")

    connection, db = connect_from_environment()

    # Define the parameters for a manual download run.
    # This dictionary allows for immediate execution of specific datasets
    # without submitting a job through the database first.
    # It maps source identifiers to their specific query configurations.

    # Configuration for HAPI Data Ingestion
    hapi_manual_config: dict[str, Any] = {
        "start_time": datetime(2025, 10, 1, 0, 0, 0, tzinfo=UTC),
        "end_time": datetime(2026, 1, 12, 0, 0, 0, tzinfo=UTC),
        "datasets": [
            "goesp_xray_flux_P1M",  # 1-minute X-Ray Flux
            "goesp_part_flux_P5M",  # 5-minute Particle Flux
        ],
    }

    # Configuration for FIDO/CDAWeb Data Ingestion
    fido_manual_config: dict[str, Any] = {
        "start_time": datetime(2025, 10, 1, 0, 0, 0, tzinfo=UTC),
        "end_time": datetime(2025, 11, 26, 0, 0, 0, tzinfo=UTC),
        "datasets": [],  # Add dataset IDs here to enable FIDO download
    }

    # Configuration for GFZ Data Ingestion
    gfz_manual_config: dict[str, Any] = {
        "start_time": datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC),
        "end_time": datetime(2026, 1, 12, 0, 0, 0, tzinfo=UTC),
        "datasets": ["Kp"],
    }

    local_manual_config: dict[str, Any] = {"datasets": ["silso_sunspot_daily", "cme_catalog"]}

    # Aggregate configurations mapped to their source IDs
    manual_jobs: dict[SourceType, dict[str, Any]] = {
        SourceType.HAPI: hapi_manual_config,
        SourceType.FIDO_CDAWEB: fido_manual_config,
        SourceType.GFZ_POTSDAM: gfz_manual_config,
        SourceType.LOCAL: local_manual_config,
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
    except Exception:
        logger.critical("Pipeline Service encountered a fatal error", exc_info=True)
        sys.exit(1)
    finally:
        # Cleanup resources
        connection.close()
        logger.info("Pipeline Service shut down.")


if __name__ == "__main__":
    main()
