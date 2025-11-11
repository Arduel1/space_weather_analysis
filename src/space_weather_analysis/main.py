import logging
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from space_weather_analysis.data_pipeline.cdaweb_pipeline import CdawebPipeline
from space_weather_analysis.database.connection import DatabaseConnection
from space_weather_analysis.etl.cme_catalog import CmeETL
from space_weather_analysis.etl.sunspots import SilsoETL

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def run_data_ingestion(config: Dict[str, Any]):
    """
    Main entry point for running the CDAWeb data pipeline.

    Args:
        config (Dict[str, Any]): A dictionary containing run parameters.
            Expected keys:
            - "START_TIME" (datetime)
            - "END_TIME" (datetime)
            - "GROUP_FILTER" (Optional[List[str]])
            - "DATASET_FILTER" (Optional[List[str]])
    """
    start_time = config.get("START_TIME")
    end_time = config.get("END_TIME")
    group_filter = config.get("GROUP_FILTER")
    dataset_filter = config.get("DATASET_FILTER")

    if not start_time or not end_time:
        logger.error("START_TIME and END_TIME must be specified in config.")
        return

    logger.info("=========================================")
    logger.info("Starting Data Ingestion Pipeline")
    logger.info(f"Time Range: {start_time} to {end_time}")

    if group_filter:
        logger.info(f"Group Filter: {group_filter}")
    else:
        logger.info("Group Filter: ALL")

    if dataset_filter:
        logger.info(f"Dataset Filter: {dataset_filter}")
    else:
        logger.info("Dataset Filter: ALL")
    logger.info("=========================================")

    start_run_time = time.time()
    try:
        pipeline = CdawebPipeline()

        pipeline.run(
            start_time=start_time,
            end_time=end_time,
            group_filter=group_filter,
            dataset_filter=dataset_filter,
        )

    except Exception as e:
        logger.critical(f"Pipeline failed with unhandled exception: {e}", exc_info=True)
    finally:
        end_run_time = time.time()
        total_time = end_run_time - start_run_time
        logger.info(f"Pipeline execution finished in {total_time:.2f} seconds.")


# def main():
#     """
#     Main entry point for the ETL application.

#     This function orchestrates the entire pipeline:
#     1. Sets up the database connection.
#     2. Initializes and runs all registered ETL processors.
#     3. Logs the final list of collections in the database.
#     """
#     logger.info("--- Application started ---")

#     db = DatabaseConnection.get_database()
#     if db is None:
#         logger.critical("Could not get database connection. Exiting.")
#         sys.exit(1)

#     logger.info(f"Successfully connected to database: {db.name}")

#     # Run ETL processes sequentially
#     SilsoETL(db).run()
#     CmeETL(db).run()

#     # Final check to log the state of the database
#     try:
#         collections = db.list_collection_names()
#         logger.info(f"Current collections: {collections}")
#     except Exception as e:
#         logger.error(f"Error listing collections: {e}")
#     finally:
#         # Ensure the database connection is always closed on exit
#         # NOTE: This right now closes the Database connection directly after the ETL Pipeline finishes.
#         # For later usages in the analysis or in notebooks this needs to be considered.
#         DatabaseConnection.close()
#         logger.info("--- Application finished ---")


if __name__ == "__main__":
    # main()
    PIPELINE_CONFIG = {
        "START_TIME": datetime(2023, 11, 1, 0, 0, 0),
        "END_TIME": datetime(2023, 11, 2, 0, 0, 0),
        "GROUP_FILTER": None,
        "DATASET_FILTER": None,
    }
    run_data_ingestion(config=PIPELINE_CONFIG)
