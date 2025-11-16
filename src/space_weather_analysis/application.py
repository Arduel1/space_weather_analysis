import logging
import time
from typing import Any, Dict

from pymongo.database import Database

from space_weather_analysis.config import (
    CATALOG_FILE,
    CME_CATALOG_FILE,
    FIDO_DOWNLOAD_DIR,
    HAPI_OUTPUT_DIR,
    HAPI_SERVER_LIST_FILE,
    SILSO_FILE,
)
from space_weather_analysis.etl.master_catalog import CatalogETL
from space_weather_analysis.etl.cme_catalog import CmeETL
from space_weather_analysis.etl.sunspots import SilsoETL
from space_weather_analysis.hapi_pipeline.discoverer import HapiDiscoverer
from space_weather_analysis.hapi_pipeline.hapi_pipeline import (
    GenericHapiPipeline,
)
from space_weather_analysis.sunpy_pipeline.cdaweb_pipeline import CdawebPipeline
from space_weather_analysis.sunpy_pipeline.discoverer import FidoCdawebDiscoverer
from space_weather_analysis.util.catalog.catalog_manager import (
    UniversalCatalogManager,
)

logger = logging.getLogger(__name__)


class Application:
    """
    Orchestrates all data processing tasks based on a run configuration.

    This class centralizes the execution logic, pulling configuration
    from the main entrypoint. It manages the execution of catalog builds,
    ETL processes, and data ingestion pipelines.
    """

    def __init__(self, db: Database, test_configs: Dict[str, Any]):
        """
        Initializes the application orchestrator.

        Args:
            db (Database): The active MongoDB database instance.
            test_configs (Dict[str, Any]): A dictionary containing specific
                                           configurations for test runs.
        """
        self.db = db
        self.test_configs = test_configs

    def run(self, run_config: Dict[str, bool]) -> None:
        """
        Runs the application tasks based on the provided configuration.

        Args:
            run_config (Dict[str, bool]): A dictionary where keys represent
                                          tasks and values (True/False)
                                          determine if they should run.
        """
        logger.info("Application run started.")

        if run_config.get("build_catalog"):
            self._run_catalog_build_task()

        if run_config.get("run_catalog_etl"):
            self._run_catalog_etl_task()

        if run_config.get("run_local_etl"):
            self._run_local_file_etl()

        if run_config.get("run_pipeline_test"):
            self._run_combined_pipeline_test()

        logger.info("Application run finished.")

    def _run_fido_file_pipeline(self, config: Dict[str, Any]) -> None:
        """
        Runs the file-download pipeline for a given configuration.

        Args:
            config (Dict[str, Any]): The configuration dictionary containing
                                     START_TIME, END_TIME, and DATASET_FILTER.
        """
        start_time = config.get("START_TIME")
        end_time = config.get("END_TIME")
        datasets_to_process = config.get("DATASET_FILTER")

        if not start_time or not end_time or not datasets_to_process:
            logger.error(
                "START_TIME, END_TIME, and DATASET_FILTER (list of datasets) must be specified for the file pipeline.",
                exc_info=True,
            )
            return

        logger.info("=========================================")
        logger.info("Starting File Ingestion Pipeline")
        logger.info(f"Time Range: {start_time} to {end_time}")
        logger.info(f"Dataset Filter: {datasets_to_process}")
        logger.info(f"Download Path: {FIDO_DOWNLOAD_DIR}")
        logger.info("=========================================")

        start_run_time = time.time()
        try:
            pipeline = CdawebPipeline(db=self.db, base_data_path=FIDO_DOWNLOAD_DIR)
            pipeline.run(
                datasets_to_process=datasets_to_process,
                start_time=start_time,
                end_time=end_time,
            )

        except Exception as e:
            logger.critical(f"File Pipeline failed: {e}", exc_info=True)
        finally:
            end_run_time = time.time()
            total_time = end_run_time - start_run_time
            logger.info(f"File Pipeline finished in {total_time:.2f} seconds.")

    def _run_generic_hapi_pipeline(self, config: Dict[str, Any]) -> None:
        """
        Runs the streaming data pipeline for a given configuration.

        Args:
            config (Dict[str, Any]): The configuration dictionary containing
                                     START_TIME, END_TIME, and DATASET_FILTER.
        """
        start_time = config.get("START_TIME")
        end_time = config.get("END_TIME")
        datasets_to_process = config.get("DATASET_FILTER")

        if not start_time or not end_time or not datasets_to_process:
            logger.error(
                "START_TIME, END_TIME, and DATASET_FILTER (list of datasets) "
                "must be specified for the streaming pipeline.",
                exc_info=True,
            )
            return

        logger.info("=========================================")
        logger.info("Starting Generic Streaming Pipeline")
        logger.info(f"Time Range: {start_time} to {end_time}")
        logger.info(f"Datasets: {datasets_to_process}")
        logger.info(f"Output Path: {HAPI_OUTPUT_DIR}")
        logger.info("=========================================")

        start_run_time = time.time()
        try:
            pipeline = GenericHapiPipeline(db=self.db, base_output_dir=HAPI_OUTPUT_DIR)
            pipeline.run(
                datasets_to_process=datasets_to_process,
                start_time=start_time,
                stop_time=end_time,
            )
        except Exception as e:
            logger.critical(f"Streaming Pipeline failed: {e}", exc_info=True)
        finally:
            end_run_time = time.time()
            total_time = end_run_time - start_run_time
            logger.info(f"Streaming Pipeline execution finished in {total_time:.2f} seconds.")

    def _run_local_file_etl(self) -> None:
        """
        Main entry point for the local file ETL application.
        """
        logger.info("--- Local ETL Application started ---")
        logger.info(f"Successfully connected to database: {self.db.name}")

        try:
            logger.info("Starting Sunspot ETL process...")
            SilsoETL(self.db, file_path=SILSO_FILE).run()
            logger.info("Finished Sunspot ETL process.")

            logger.info("Starting CME Catalog ETL process...")
            CmeETL(self.db, file_path=CME_CATALOG_FILE).run()
            logger.info("Finished CME Catalog ETL process.")

            collections = self.db.list_collection_names()
            logger.info(f"Current collections: {collections}")
        except Exception as e:
            logger.error(f"Error during local ETL: {e}", exc_info=True)
        finally:
            logger.info("--- Local ETL Application finished ---")

    def _run_catalog_build_task(self) -> None:
        """
        Initializes and runs the universal catalog build process.

        This function instantiates the required discoverer strategies
        and injects them into the UniversalCatalogManager.
        """
        logger.info("--- Running: Universal Catalog Build ---")

        try:
            # Instantiate the concrete discoverer strategies
            hapi_discoverer = HapiDiscoverer(
                server_list_path=HAPI_SERVER_LIST_FILE,
                include_test_servers=True,
            )
            fido_discoverer = FidoCdawebDiscoverer()

            discoverers = [hapi_discoverer, fido_discoverer]

            manager = UniversalCatalogManager(discoverers=discoverers, catalog_output_path=CATALOG_FILE)
            manager.build_catalog()
        except Exception as e:
            logger.error(f"Error during Universal Catalog Build: {e}", exc_info=True)
        finally:
            logger.info("--- Finished: Universal Catalog Build ---")

    def _run_catalog_etl_task(self) -> None:
        """
        Runs the ETL process for the master catalog.
        This task loads the generated catalog file into MongoDB.
        """
        logger.info("--- Running: Master Catalog ETL ---")
        try:
            CatalogETL(self.db, file_path=CATALOG_FILE).run()
        except Exception as e:
            logger.error(f"Error during catalog ETL: {e}", exc_info=True)
        finally:
            logger.info("--- Finished: Master Catalog ETL ---")

    def _run_combined_pipeline_test(self) -> None:
        """
        Runs a test query for one or more pipelines
        to ensure the MongoDB-driven pipelines are working.
        """
        logger.info("===== Starting Combined Pipeline Test =====")

        hapi_config = self.test_configs.get("hapi_config")
        if hapi_config:
            logger.info(
                f"--- [Test 1/2] Running Generic Streaming Pipeline for '{hapi_config.get('DATASET_FILTER')}' ---"
            )
            self._run_generic_hapi_pipeline(config=hapi_config)
        else:
            logger.warning("No HAPI test configuration found. Skipping.")

        # fido_config = self.test_configs.get("fido_config")
        # if fido_config:
        #     logger.info(
        #         f"--- [Test 2/2] Running File (CDAWeb) Pipeline for "
        #         f"'{fido_config.get('DATASET_FILTER')}' ---"
        #     )
        #     self._run_fido_file_pipeline(config=fido_config)
        # else:
        #     logger.warning("No FIDO test configuration found. Skipping.")

        logger.info("===== Combined Pipeline Test Finished =====")
