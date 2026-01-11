import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from dotenv import load_dotenv

from space_weather_analysis.application import Application
from space_weather_analysis.database.connection import DatabaseConnection

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main() -> None:
    """
    Executes the main application lifecycle.

    This function sets up the runtime environment, defines the execution
    configuration for ETL and pipeline tasks, and delegates control to
    the Application orchestrator.
    """
    logger.info("Main application entry point initialized.")

    RUN_CONFIGURATION: Dict[str, bool] = {
        "run_catalog_etl": True,
        "run_local_etl": True,
        "run_pipeline_test": True,
    }

    # --- Test Configurations ---
    # Define parameters for the unified pipeline tests.

    # Configuration for HAPI Data Ingestion
    hapi_test_config: Dict[str, Any] = {
        "START_TIME": datetime(2023, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        "END_TIME": datetime(2025, 11, 26, 0, 0, 0, tzinfo=timezone.utc),
        "DATASET_FILTER": [
            "goesp_xray_flux_P1M",  # 1-minute X-Ray Flux
            "goesp_part_flux_P5M",  # 5-minute Particle Flux
        ],
    }

    # Configuration for FIDO/CDAWeb Data Ingestion
    fido_test_config: Dict[str, Any] = {}

    # Aggregate test configurations for injection into the application.
    TEST_CONFIGS: Dict[str, Any] = {
        "hapi_config": hapi_test_config,
        "fido_config": fido_test_config,
    }

    db = DatabaseConnection.get_database()
    if db is None:
        logger.critical("Database connection could not be established. Terminating execution.")
        sys.exit(1)

    try:
        # Instantiate and run the main application logic.
        app = Application(db, TEST_CONFIGS)
        app.run(RUN_CONFIGURATION)

    except Exception as e:
        logger.critical(
            f"An unrecoverable error occurred during application execution: {e}",
            exc_info=True,
        )
    finally:
        logger.info("Closing global database connection.")
        DatabaseConnection.close()

    logger.info("Main application entry point execution finished.")


if __name__ == "__main__":
    main()
