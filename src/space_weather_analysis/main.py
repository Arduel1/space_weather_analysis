import logging
import sys
import time
from datetime import datetime
from typing import Any, Dict

from dotenv import load_dotenv

from space_weather_analysis.config import (
    CATALOG_FILE,
    CME_CATALOG_FILE,
    FIDO_DOWNLOAD_DIR,
    HAPI_OUTPUT_DIR,
    HAPI_SERVER_LIST_FILE,
    SILSO_FILE,
)
from space_weather_analysis.database.connection import DatabaseConnection
from space_weather_analysis.etl.cme_catalog import CmeETL
from space_weather_analysis.etl.sunspots import SilsoETL
from space_weather_analysis.hapi_pipeline.discoverer import HapiDiscoverer
from space_weather_analysis.hapi_pipeline.hapi_pipeline import GenericHapiPipeline
from space_weather_analysis.sunpy_pipeline.cdaweb_pipeline import CdawebPipeline
from space_weather_analysis.sunpy_pipeline.discoverer import FidoCdawebDiscoverer
from space_weather_analysis.util.catalog.catalog_manager import UniversalCatalogManager

# Load environment variables
load_dotenv()

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def run_fido_file_pipeline(config: Dict[str, Any]):
    """
    Main entry point for running the ORIGINAL Fido file-download pipeline.

    Args:
        config (Dict[str, Any]): The configuration dictionary.
    """
    start_time = config.get("START_TIME")
    end_time = config.get("END_TIME")
    group_filter = config.get("GROUP_FILTER")
    dataset_filter = config.get("DATASET_FILTER")

    if not start_time or not end_time:
        logger.error("START_TIME and END_TIME must be specified in config.", exc_info=True)
        return

    logger.info("=========================================")
    logger.info("Starting Fido (File) Ingestion Pipeline")
    logger.info(f"Time Range: {start_time} to {end_time}")
    logger.info(f"Group Filter: {group_filter or 'ALL'}")
    logger.info(f"Dataset Filter: {dataset_filter or 'ALL'}")
    logger.info(f"Download Path: {FIDO_DOWNLOAD_DIR}")
    logger.info("=========================================")

    start_run_time = time.time()
    try:
        # Pass the specific download path (from config) to the pipeline
        pipeline = CdawebPipeline(base_data_path=FIDO_DOWNLOAD_DIR)
        pipeline.run(
            start_time=start_time,
            end_time=end_time,
            group_filter=group_filter,
            dataset_filter=dataset_filter,
        )

    except Exception as e:
        logger.critical(f"Fido Pipeline failed: {e}", exc_info=True)
    finally:
        end_run_time = time.time()
        total_time = end_run_time - start_run_time
        logger.info(f"Fido Pipeline finished in {total_time:.2f} seconds.")


def run_generic_hapi_pipeline(config: Dict[str, Any]):
    """
    Main entry point for running the NEW Generic HAPI streaming pipeline.

    Args:
        config (Dict[str, Any]): The configuration dictionary.
    """
    start_time = config.get("START_TIME")
    end_time = config.get("END_TIME")
    datasets_to_process = config.get("DATASET_FILTER")

    if not start_time or not end_time or not datasets_to_process:
        logger.error(
            "START_TIME, END_TIME, and DATASET_FILTER (list of datasets) must be specified for the HAPI pipeline.",
            exc_info=True,
        )
        return

    logger.info("=========================================")
    logger.info("Starting Generic HAPI Pipeline")
    logger.info(f"Time Range: {start_time} to {end_time}")
    logger.info(f"Datasets: {datasets_to_process}")
    logger.info(f"Catalog File: {CATALOG_FILE}")
    logger.info(f"Output Path: {HAPI_OUTPUT_DIR}")
    logger.info("=========================================")

    start_run_time = time.time()
    try:
        # Pass config paths to the pipeline
        pipeline = GenericHapiPipeline(catalog_path=CATALOG_FILE, base_output_dir=HAPI_OUTPUT_DIR)
        pipeline.run(
            datasets_to_process=datasets_to_process,
            start_time=start_time,
            stop_time=end_time,
        )
    except Exception as e:
        logger.critical(f"HAPI Pipeline failed: {e}", exc_info=True)
    finally:
        end_run_time = time.time()
        total_time = end_run_time - start_run_time
        logger.info(f"HAPI Pipeline execution finished in {total_time:.2f} seconds.")


def run_local_file_etl():
    """
    Main entry point for the local file ETL application (SILSO, CME).
    """
    logger.info("--- Local ETL Application started ---")
    db = DatabaseConnection.get_database()
    if db is None:
        logger.critical("Could not get database connection. Exiting.")
        sys.exit(1)

    logger.info(f"Successfully connected to database: {db.name}")

    try:
        # Run ETL processes sequentially, injecting the file paths from config
        SilsoETL(db, file_path=SILSO_FILE).run()
        CmeETL(db, file_path=CME_CATALOG_FILE).run()

        collections = db.list_collection_names()
        logger.info(f"Current collections: {collections}")
    except Exception as e:
        logger.error(f"Error during local ETL: {e}", exc_info=True)
    finally:
        DatabaseConnection.close()
        logger.info("--- Local ETL Application finished ---")


def run_catalog_build_task():
    """
    Initializes and runs the universal catalog build process.

    This function instantiates the required discoverer strategies
    and injects them into the UniversalCatalogManager.
    """
    logger.info("--- Running: Universal Catalog Build ---")

    # Instantiate the concrete discoverer strategies
    hapi_discoverer = HapiDiscoverer(
        server_list_path=HAPI_SERVER_LIST_FILE,
        include_test_servers=True,  # Include TestData for debugging
    )
    fido_discoverer = FidoCdawebDiscoverer()

    # Create the list of discoverers to inject
    discoverers = [hapi_discoverer, fido_discoverer]

    # Instantiate the manager with the discoverers
    manager = UniversalCatalogManager(discoverers=discoverers, catalog_output_path=CATALOG_FILE)

    # Run the build process
    manager.build_catalog()
    logger.info("--- Finished: Universal Catalog Build ---")


def main():
    """
    Main entry point for the application.
    """
    logger.info("--- Application Main Entry Point Started ---")

    # Build Universal Catalog
    # Run this once to create 'hapi_master_catalog.json'.
    # logger.info("Running: Universal Catalog Build")
    # run_catalog_build_task()

    # # --- Shared Pipeline Configuration ---
    # PIPELINE_CONFIG = {
    #     "START_TIME": datetime(1998, 1, 1, 0, 0, 0),
    #     "END_TIME": datetime(1998, 1, 2, 0, 0, 0),
    #     # "GROUP_FILTER": ["ACE"],
    #     # This dataset is now in the catalog from two sources:
    #     # 1. HAPI (from cdaweb.gsfc.nasa.gov)
    #     # 2. FIDO_CDAWEB (from group "ACE")
    #     "DATASET_FILTER": ["AC_OR_SSC"],
    # }

    # --- Action 2: Run Generic HAPI (Streaming) Pipeline ---
    # This will use the new catalog and find the HAPI source for AC_H0_MFI
    #
    # logger.info("Running: Generic HAPI Pipeline")
    # run_generic_hapi_pipeline(config=PIPELINE_CONFIG)

    # --- Action 3: Run Fido (File Download) Pipeline ---
    # (This is the original CDAWeb pipeline, retained for non-HAPI sources)
    #
    # logger.info("Running: Fido File Pipeline")
    # run_fido_file_pipeline(config=PIPELINE_CONFIG)

    # --- Action 4: Run Local ETL (CME, SILSO) ---
    # (This populates the database from local files in /data)
    #
    logger.info("Running: Local ETL Processes")
    run_local_file_etl()

    logger.info("--- Application Main Entry Point Finished ---")


if __name__ == "__main__":
    main()
