import logging
import pathlib
from datetime import datetime
from typing import Callable, List, Optional

from pymongo.database import Database

from src.pipeline_service.database.mongo_loader import MongoLoader
from src.pipeline_service.pipelines.base import BasePipeline
from src.pipeline_service.util.documents import dataframe_to_documents
from src.pipeline_service.util.naming import sanitize_collection_name
from src.space_weather_analysis.clients.gfz import GfzClient

logger = logging.getLogger(__name__)


class GfzPipeline(MongoLoader, BasePipeline):
    """
    Pipeline implementation for GFZ Potsdam data ingestion.

    This pipeline handles the downloading of geomagnetic indices from GFZ
    and stores them in MongoDB.
    """

    def __init__(self, db: Database, base_data_path: pathlib.Path):
        """
        Initializes the GFZ pipeline.

        Args:
            db: The database connection instance.
            base_data_path: Root directory for storing downloaded files.
        """
        super().__init__(db)
        self.db = db
        self.base_data_path = base_data_path
        self.client = GfzClient()

    def run(
        self,
        datasets: List[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> None:
        """
        Executes the download process for the requested GFZ datasets.

        Args:
            datasets: List of index names (e.g., 'Kp').
            start_time: Start of the data range.
            end_time: End of the data range.
            progress_callback: Optional callback to report progress.
        """
        if not start_time or not end_time:
            logger.error("Start and end time are required for GFZ pipeline.")
            return

        total_datasets = len(datasets)

        for i, dataset_id in enumerate(datasets):
            logger.info(f"Processing GFZ dataset: {dataset_id}")

            if progress_callback:
                progress = i / total_datasets
                progress_callback(progress, f"Fetching GFZ data: {dataset_id}")

            try:
                df = self.client.get_data(dataset_id, start_time, end_time)

                if df.empty:
                    logger.warning(f"No data returned for {dataset_id}")
                    continue

                collection_name = sanitize_collection_name(dataset_id)
                self._ensure_timeseries_collection(collection_name, time_field="timestamp", meta_field="metadata")

                documents = dataframe_to_documents(df, f"GFZ_POTSDAM/{dataset_id}")

                if documents:
                    self._load_data(collection_name, documents)

            except Exception as e:
                logger.error(f"Failed to process {dataset_id}: {e}", exc_info=True)
