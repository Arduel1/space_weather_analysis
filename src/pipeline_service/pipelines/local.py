import logging
import pathlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from types import MappingProxyType

import pandas as pd

from pipeline_service.parsing.cme_catalog import CME_PROVIDER_ID, parse_cme_catalog
from pipeline_service.parsing.silso import SILSO_PROVIDER_ID, parse_silso_daily, parse_silso_monthly
from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.chunks import ingest_or_record_gap, refresh_coverage
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.timeseries.documents import frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.naming import sanitize_collection_name

logger = logging.getLogger(__name__)

# A file holds the whole time range of its dataset, so a failed file is a gap over all time.
WHOLE_FILE_START = datetime.min.replace(tzinfo=UTC)
WHOLE_FILE_STOP = datetime.max.replace(tzinfo=UTC)

# Sunspot numbers and CME events lie hours to months apart, hours is the coarsest MongoDB granularity.
LOCAL_GRANULARITY = Granularity.HOURS


@dataclass(frozen=True)
class LocalDataset:
    """A dataset delivered as one complete file in the data directory.

    Attributes:
        file_name: The name of the file in the data directory.
        parse: Reads the file into a frame with a DatetimeIndex and one column per stored data field.
        provider_id: The ID of the dataset at its provider, part of the source identifier of the documents.
    """

    file_name: str
    parse: Callable[[pathlib.Path], pd.DataFrame]
    provider_id: str


# The local datasets, keyed by dataset ID.
LOCAL_DATASETS: Mapping[str, LocalDataset] = MappingProxyType(
    {
        "silso_sunspot_daily": LocalDataset("SN_d_tot_V2.0.csv", parse_silso_daily, SILSO_PROVIDER_ID),
        "silso_sunspot_monthly": LocalDataset("SN_m_tot_V2.0.csv", parse_silso_monthly, SILSO_PROVIDER_ID),
        "silso_sunspot_smoothed": LocalDataset("SN_ms_tot_V2.0.csv", parse_silso_monthly, SILSO_PROVIDER_ID),
        "cme_catalog": LocalDataset("cme_catalog.txt", parse_cme_catalog, CME_PROVIDER_ID),
    }
)


class LocalPipeline:
    """Ingests the datasets of complete files in the data directory, each file as one chunk.

    The files hold the whole dataset, so the requested time range is not used and every run replaces the stored documents.
    """

    def __init__(self, *, timeseries: TimeSeriesRepository, coverage_repository: CoverageRepository, data_dir: pathlib.Path) -> None:
        """Initializes the local pipeline.

        Args:
            timeseries: The repository storing the data documents.
            coverage_repository: The repository storing which time ranges are stored.
            data_dir: The directory holding the dataset files.
        """
        self.timeseries = timeseries
        self.coverage_repository = coverage_repository
        self.data_dir = data_dir

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
            collection = sanitize_collection_name(dataset_id)
            ingest = partial(self._replace_with_file, LOCAL_DATASETS[dataset_id], collection)
            if ingest_or_record_gap(report, dataset_id, WHOLE_FILE_START, WHOLE_FILE_STOP, ingest) is not None:
                refresh_coverage(self.coverage_repository, dataset_id, collection)
        return report

    def _replace_with_file(self, dataset: LocalDataset, collection: str) -> int:
        """Replaces the stored documents of a dataset with the documents built from its whole file.

        The file is read completely before the stored documents are deleted, so an unreadable file keeps them.

        Args:
            dataset: The local dataset.
            collection: The collection holding the data documents.

        Returns:
            The number of stored documents.

        Raises:
            FileNotFoundError: If the file does not exist.
            DataProcessingError: If the file cannot be read or holds no valid row.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        frame = dataset.parse(self.data_dir / dataset.file_name)
        documents = frame_to_documents(frame, source_identifier(SourceType.LOCAL, dataset.provider_id))
        self.timeseries.ensure_collection(collection, LOCAL_GRANULARITY)
        stored_count = self.timeseries.replace_all(collection, documents)
        logger.info("Replaced the content of %s with %d documents", collection, stored_count)
        return stored_count
