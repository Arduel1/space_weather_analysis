import logging
import pathlib
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, cast

from pymongo.database import Database

from space_weather_analysis.config import CATALOG_FILE, FIDO_CDAWEB_SOURCE_ID, HAPI_SOURCE_ID
from space_weather_analysis.discovery.loader import CatalogLoader
from space_weather_analysis.pipelines.factory import PipelineFactory

logger = logging.getLogger(__name__)


class Application:
    """
    Orchestrates data processing and ingestion tasks.

    This class serves as the control center for the application, interpreting
    configuration directives to trigger specific workflows. It manages
    dependencies such as database connections and ensures that ETL processes
    and pipelines are executed in the correct order.
    """

    def __init__(self, db: Database, test_configs: Dict[str, Any]) -> None:
        """
        Initializes the application orchestrator.

        Args:
            db (Database): The active MongoDB database instance used for
                data persistence.
            test_configs (Dict[str, Any]): Configuration overrides and parameters
                for pipeline test runs, allowing specific time ranges and
                datasets to be targeted.
        """
        self.db = db
        self.test_configs = test_configs

    def run(self, run_config: Dict[str, bool]) -> None:
        """
        Executes the application workflows based on the enabled flags.

        This method checks the configuration dictionary to determine which
        sub-tasks (Catalog ETL, Local ETL, Pipeline Tests) should be executed.
        Each task is run sequentially.

        Args:
            run_config (Dict[str, bool]): A dictionary acting as a feature toggle
                for the various application tasks.
        """
        logger.info("Application run sequence started.")

        if run_config.get("run_catalog_etl"):
            self._run_catalog_etl_task()

        if run_config.get("run_local_etl"):
            self._run_local_file_etl()

        if run_config.get("run_pipeline_test"):
            self._run_unified_pipeline_test()

        logger.info("Application run sequence finished.")

    def _run_local_file_etl(self) -> None:
        """
        Executes ETL processes for local static files.

        This method instantiates the pipeline factory and triggers processing
        for predefined local datasets, such as SILSO sunspot data and CME catalogs.
        A temporary directory is used to initialize the factory context, although
        local file processing primarily relies on static paths defined in the config.
        """
        logger.info("Starting Local File ETL process.")

        with tempfile.TemporaryDirectory() as temp_dir:
            factory = PipelineFactory(self.db, pathlib.Path(temp_dir))

            local_jobs = ["LOCAL_SILSO", "LOCAL_CME"]

            for job_type in local_jobs:
                try:
                    logger.info(f"Initiating pipeline execution for job: {job_type}")
                    pipeline = factory.get_pipeline(job_type)
                    # Local pipelines are designed to run without specific time filters,
                    # processing the entire available file content.
                    pipeline.run([], None, None)
                    logger.info(f"Pipeline execution completed for job: {job_type}")
                except Exception as e:
                    logger.error(f"Pipeline execution failed for {job_type}: {e}", exc_info=True)

        logger.info("Local File ETL process finished.")

    def _run_catalog_etl_task(self) -> None:
        """
        Loads the Master Catalog into the database.

        This task reads the generated JSON catalog file and synchronizes it with
        the MongoDB collection. It ensures that the database has an up-to-date
        index of all available datasets before any pipelines attempt to process them.
        """
        logger.info("Starting Master Catalog ETL task.")
        try:
            # The loader handles the reading of the JSON file and the upsert logic
            # to avoid duplicate entries in the database.
            CatalogLoader(self.db, file_path=CATALOG_FILE).run()
        except Exception as e:
            logger.error(f"Critical error during catalog ETL: {e}", exc_info=True)
        finally:
            logger.info("Master Catalog ETL task finished.")

    def _run_unified_pipeline_test(self) -> None:
        """
        Executes test runs for unified data pipelines.

        This method iterates through configured test scenarios and executes
        the corresponding pipelines using the factory pattern.
        It is useful for validating pipeline functionality and ingesting
        specific subsets of data defined in the test configuration.
        """
        logger.info("Starting Unified Pipeline Test sequence.")

        jobs: List[Tuple[str, str]] = []

        # Determine which pipeline tests are configured to run
        if self.test_configs.get("hapi_config"):
            jobs.append(("hapi_config", HAPI_SOURCE_ID))
        else:
            logger.warning("HAPI configuration missing; skipping HAPI pipeline test.")

        if self.test_configs.get("fido_config"):
            jobs.append(("fido_config", FIDO_CDAWEB_SOURCE_ID))
        else:
            logger.warning("FIDO configuration missing; skipping FIDO pipeline test.")

        # Establish a temporary context for any file-based operations required
        # by the pipelines during execution.
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_path = pathlib.Path(temp_dir_str)
            logger.info(f"Initialized temporary working directory: {temp_path}")

            factory = PipelineFactory(self.db, temp_path)

            for config_key, source_type in jobs:
                config: Dict[str, Any] = self.test_configs.get(config_key, {})

                # Type casting is used here to ensure strict typing compliance,
                # assuming the configuration dictionary matches the expected structure.
                datasets = cast(List[str], config.get("DATASET_FILTER"))
                start_time = cast(Optional[datetime], config.get("START_TIME"))
                end_time = cast(Optional[datetime], config.get("END_TIME"))

                if not datasets:
                    logger.warning(f"Skipping {source_type}: Configuration incomplete (missing dataset filter).")
                    continue

                logger.info(f"Executing pipeline for source: {source_type}")
                start_run_time = time.time()

                try:
                    pipeline = factory.get_pipeline(source_type)
                    pipeline.run(datasets, start_time, end_time)

                except Exception as e:
                    logger.critical(f"Pipeline execution failed critically for {source_type}: {e}", exc_info=True)
                finally:
                    duration = time.time() - start_run_time
                    logger.info(f"Finished {source_type} execution in {duration:.2f} seconds.")

        logger.info("Unified Pipeline Test sequence finished.")
