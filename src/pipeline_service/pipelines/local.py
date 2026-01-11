import logging
import pathlib
from datetime import datetime
from typing import Callable, List, Optional

from pymongo.database import Database

from pipeline_service.pipelines.base import BasePipeline
from pipeline_service.pipelines.processors.cme import CmeETL
from pipeline_service.pipelines.processors.sunspots import SilsoETL

logger = logging.getLogger(__name__)


class LocalPipeline(BasePipeline):
    """
    Pipeline for processing local datasets.

    Unlike remote pipelines, this pipeline ignores start/end times as it
    processes complete static files provided in the data directory.
    """

    def __init__(self, db: Database, base_data_path: pathlib.Path) -> None:
        """
        Initializes the local pipeline.

        Args:
            db: MongoDB database instance.
            base_data_path: Root directory for data .
        """
        self.db = db
        self.base_data_path = base_data_path

    def run(
        self,
        datasets: List[str],
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> None:
        """
        Triggers specific local processors based on the requested dataset ID.

        Args:
            datasets: List of dataset IDs (e.g., 'silso_sunspot_daily').
            start_time: Ignored for local static files.
            end_time: Ignored for local static files.
            progress_callback: Optional callback for progress reporting.
        """
        logger.info(f"Running Local Pipeline for datasets: {datasets}")

        total_datasets = len(datasets)

        for i, dataset_id in enumerate(datasets):
            if progress_callback:
                progress = i / total_datasets
                progress_callback(progress, f"Processing local file for: {dataset_id}")

            try:
                if dataset_id == "silso_sunspot_daily":
                    file_path = self.base_data_path / "SN_d_tot_V2.0.csv"
                    processor = SilsoETL(self.db, file_path)
                    processor.run()

                elif dataset_id == "cme_catalog":
                    file_path = self.base_data_path / "cme_catalog.txt"
                    processor = CmeETL(self.db, file_path)
                    processor.run()

                else:
                    logger.warning(f"Unknown local dataset requested: {dataset_id}")

            except Exception as e:
                logger.error(f"Failed to process local dataset {dataset_id}: {e}", exc_info=True)
