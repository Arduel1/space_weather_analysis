import logging
import tempfile
import pathlib
import time
from typing import Any, Dict, List, Tuple

from pymongo.database import Database

from space_weather_analysis.config import (
    CATALOG_FILE,
    HAPI_SERVER_LIST_FILE,
    HAPI_SOURCE_ID,
    FIDO_CDAWEB_SOURCE_ID,
)
from space_weather_analysis.discovery.loader import CatalogLoader
from space_weather_analysis.discovery.discoverers.hapi import HapiDiscoverer
from space_weather_analysis.discovery.discoverers.cdaweb import CdawebDiscoverer
from space_weather_analysis.discovery.builder import CatalogBuilder
from space_weather_analysis.pipelines.factory import PipelineFactory

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
            self._run_unified_pipeline_test()

        logger.info("Application run finished.")

    def _run_local_file_etl(self) -> None:
        """
        Runs the local file ETLs using the factory mechanism.
        """
        logger.info("--- Local ETL Application started ---")
        # We use a temporary context just to initialize the factory,
        # though local files don't strictly need a temp dir.
        with tempfile.TemporaryDirectory() as temp_dir:
            factory = PipelineFactory(self.db, pathlib.Path(temp_dir))

            local_jobs = ["LOCAL_SILSO", "LOCAL_CME"]

            for job_type in local_jobs:
                try:
                    logger.info(f"Starting pipeline for {job_type}...")
                    pipeline = factory.get_pipeline(job_type)
                    # Local pipelines ignore arguments, so we pass empty/dummy values
                    pipeline.run([], None, None)
                    logger.info(f"Finished pipeline for {job_type}.")
                except Exception as e:
                    logger.error(f"Error running local pipeline {job_type}: {e}", exc_info=True)

        logger.info("--- Local ETL Application finished ---")

    def _run_catalog_build_task(self) -> None:
        """
        Initializes and runs the universal catalog build process.
        """
        logger.info("--- Running: Universal Catalog Build ---")

        try:
            hapi_discoverer = HapiDiscoverer(
                server_list_path=HAPI_SERVER_LIST_FILE,
                include_test_servers=True,
            )
            fido_discoverer = CdawebDiscoverer()

            discoverers = [hapi_discoverer, fido_discoverer]

            manager = CatalogBuilder(discoverers=discoverers, catalog_output_path=CATALOG_FILE)
            manager.build_catalog()
        except Exception as e:
            logger.error(f"Error during Universal Catalog Build: {e}", exc_info=True)
        finally:
            logger.info("--- Finished: Universal Catalog Build ---")

    def _run_catalog_etl_task(self) -> None:
        """
        Runs the ETL process for the master catalog.
        """
        logger.info("--- Running: Master Catalog ETL ---")
        try:
            CatalogLoader(self.db, file_path=CATALOG_FILE).run()
        except Exception as e:
            logger.error(f"Error during catalog ETL: {e}", exc_info=True)
        finally:
            logger.info("--- Finished: Master Catalog ETL ---")

    def _run_unified_pipeline_test(self) -> None:
        """
        Runs a test query for one or more pipelines using the unified Factory approach.
        """
        logger.info("===== Starting Unified Pipeline Test =====")

        # Define jobs based on config availability
        jobs: List[Tuple[str, str]] = []

        if self.test_configs.get("hapi_config"):
            jobs.append(("hapi_config", HAPI_SOURCE_ID))
        else:
            logger.warning("No HAPI test configuration found. Skipping.")

        if self.test_configs.get("fido_config"):
            jobs.append(("fido_config", FIDO_CDAWEB_SOURCE_ID))
        else:
            logger.warning("No FIDO test configuration found. Skipping.")

        # Use a temporary directory context for file-based pipelines
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_path = pathlib.Path(temp_dir_str)
            logger.info(f"Using temporary path for file pipelines: {temp_path}")

            # Initialize the Factory once
            factory = PipelineFactory(self.db, temp_path)

            for config_key, source_type in jobs:
                config = self.test_configs.get(config_key, {})
                datasets = config.get("DATASET_FILTER")
                start_time = config.get("START_TIME")
                end_time = config.get("END_TIME")

                if not datasets or not start_time or not end_time:
                    logger.warning(f"Skipping {source_type}: Incomplete configuration.")
                    continue

                logger.info(f"--- Running Pipeline for {source_type} ---")
                start_run_time = time.time()

                try:
                    # Get the correct pipeline strategy
                    pipeline = factory.get_pipeline(source_type)

                    # Execute securely via interface
                    pipeline.run(datasets, start_time, end_time)

                except Exception as e:
                    logger.critical(f"Pipeline execution failed for {source_type}: {e}", exc_info=True)
                finally:
                    duration = time.time() - start_run_time
                    logger.info(f"--- Finished {source_type} in {duration:.2f}s ---")

        logger.info("===== Unified Pipeline Test Finished =====")
