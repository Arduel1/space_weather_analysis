import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from dotenv import load_dotenv

from space_weather_analysis.application import Application
from space_weather_analysis.database.connection import DatabaseConnection

load_dotenv()

# --- Logging Configuration ---
# Configure logging at the application's entry point
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main() -> None:
    """
    Main application entry point.

    This function defines the run configuration, establishes the
    database connection, and passes control to the Application orchestrator.
    It ensures the database connection is closed upon exit.
    """
    logger.info("--- Application Main Entry Point Started ---")

    # --- Run Configuration ---
    # Define which tasks to run.
    RUN_CONFIGURATION: Dict[str, bool] = {
        "build_catalog": False,
        "run_catalog_etl": True,
        "run_local_etl": False,
        "run_pipeline_test": True,
    }

    # --- Test Configurations ---
    # Define specific configurations for pipeline tests.
    # Note: We explicitly attach timezone.utc to all datetimes to ensure
    # consistent storage and avoid "naive datetime" warnings or conversion errors.

    hapi_test_config: Dict[str, Any] = {
        "START_TIME": datetime(2023, 11, 22, 0, 0, 0, tzinfo=timezone.utc),
        "END_TIME": datetime(2023, 11, 24, 0, 0, 0, tzinfo=timezone.utc),
        "DATASET_FILTER": ["aaa/baseline_all/PT1M/NEZ"],
    }

    fido_test_config: Dict[str, Any] = {
        "START_TIME": datetime(2023, 11, 22, 0, 0, 0, tzinfo=timezone.utc),
        "END_TIME": datetime(2023, 11, 24, 0, 0, 0, tzinfo=timezone.utc),
        "DATASET_FILTER": ["aaa/baseline_all/PT1M/NEZ", "AWE_L3A_TMP", "AWE_L3C_Q20"],
    }

    TEST_CONFIGS: Dict[str, Any] = {
        "hapi_config": hapi_test_config,
        "fido_config": fido_test_config,
    }

    # --- Database Connection Lifecycle ---
    # Manage the connection lifecycle for all DB-dependent tasks.
    db = DatabaseConnection.get_database()
    if db is None:
        logger.critical("Failed to get database connection. Exiting all DB-dependent tasks.")
        sys.exit(1)

    try:
        # --- Application Execution ---
        # Instantiate the main application and run it with the config.
        app = Application(db, TEST_CONFIGS)
        app.run(RUN_CONFIGURATION)

    except Exception as e:
        logger.critical(
            f"A high-level error occurred during application run: {e}",
            exc_info=True,
        )
    finally:
        logger.info("Closing global database connection.")
        DatabaseConnection.close()

    logger.info("--- Application Main Entry Point Finished ---")


if __name__ == "__main__":
    main()
