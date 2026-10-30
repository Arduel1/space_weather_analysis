import logging
import pathlib
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType

from pymongo.database import Database

from pipeline_service.pipelines.base import BasePipeline, ProgressCallback
from pipeline_service.pipelines.processors.base import BaseETLProcessor
from pipeline_service.pipelines.processors.cme import CmeETL
from pipeline_service.pipelines.processors.sunspots import SilsoETL
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.coverage.repository import CoverageRepository, MongoCoverageRepository

logger = logging.getLogger(__name__)

# A file holds the whole time range of its dataset, so a failed file is a gap over all time.
WHOLE_FILE_START = datetime.min.replace(tzinfo=UTC)
WHOLE_FILE_STOP = datetime.max.replace(tzinfo=UTC)

# The local datasets with their processor and the name of their file in the data directory.
LOCAL_DATASETS: Mapping[str, tuple[type[BaseETLProcessor], str]] = MappingProxyType(
    {
        "silso_sunspot_daily": (SilsoETL, "SN_d_tot_V2.0.csv"),
        "cme_catalog": (CmeETL, "cme_catalog.txt"),
    }
)


class LocalPipeline(BasePipeline):
    """Ingests the datasets of complete files in the data directory, each file as one chunk.

    The files hold the whole dataset, so the requested time range is not used.
    """

    def __init__(self, db: Database, data_dir: pathlib.Path) -> None:
        """Initializes the local pipeline.

        Args:
            db: The MongoDB database instance.
            data_dir: The directory holding the dataset files.
        """
        self.db = db
        self.data_dir = data_dir
        self.coverage_repository: CoverageRepository = MongoCoverageRepository(db)

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Replaces the stored documents of each local dataset with the content of its file.

        Args:
            datasets: The IDs of local datasets, keys of LOCAL_DATASETS.
            start_time: Not used, as each file holds the whole dataset.
            end_time: Not used, as each file holds the whole dataset.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with one chunk per dataset and the datasets whose file could not be ingested.

        Raises:
            ValueError: If no dataset is given or a dataset ID is no local dataset.
            FileNotFoundError: If the file of a dataset does not exist.
        """
        del start_time, end_time
        if not datasets:
            raise ValueError("The local pipeline needs at least one dataset.")
        unknown_datasets = sorted(set(datasets) - LOCAL_DATASETS.keys())
        if unknown_datasets:
            raise ValueError(f"Unknown local datasets {unknown_datasets}; known are {sorted(LOCAL_DATASETS)}.")

        report = IngestionReport()
        for dataset_index, dataset_id in enumerate(datasets):
            if progress_callback:
                progress_callback(dataset_index / len(datasets), f"Processing local file for: {dataset_id}")
            processor_class, file_name = LOCAL_DATASETS[dataset_id]
            processor = processor_class(self.db, self.data_dir / file_name)
            stored_count = self._ingest_chunk(report, dataset_id, WHOLE_FILE_START, WHOLE_FILE_STOP, processor.run)
            if stored_count is not None:
                self._refresh_coverage(self.coverage_repository, dataset_id, processor_class.COLLECTION_NAME)
        return report
