import logging
import pathlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from types import MappingProxyType

import pandas as pd
import requests

from pipeline_service.parsing.cme_catalog import CME_PROVIDER_ID, parse_cme_catalog
from pipeline_service.parsing.silso import SILSO_PROVIDER_ID, parse_silso_daily, parse_silso_monthly
from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.chunks import METADATA_SAVE_ERRORS, ingest_or_record_gap, refresh_coverage
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.download import download_file
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.metadata.minimal import build_minimal_metadata
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.timeseries.documents import frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.error_handling import DataRetrievalError
from space_weather_analysis.util.naming import sanitize_collection_name

logger = logging.getLogger(__name__)

# A file holds the whole time range of its dataset, so its documents replace all time and a failed file is a gap over all time.
WHOLE_FILE_START = datetime.min.replace(tzinfo=UTC)
WHOLE_FILE_STOP = datetime.max.replace(tzinfo=UTC)

# Sunspot numbers and CME events lie hours to months apart, hours is the coarsest MongoDB granularity.
LOCAL_GRANULARITY = Granularity.HOURS
# Age from which a file with download URL is fetched again; the providers update their files at most daily
FILE_MAX_AGE = timedelta(days=1)
# Connect timeout and longest wait for the next chunk of a file download, in seconds
DOWNLOAD_TIMEOUT = (10.0, 60.0)


@dataclass(frozen=True)
class LocalDataset:
    """A dataset delivered as one complete file in the data directory.

    Attributes:
        file_name: The name of the file in the data directory.
        parse: Reads the file into a frame with a DatetimeIndex and one column per stored data field.
        provider_id: The ID of the dataset at its provider, part of the source identifier of the documents.
        url: Where the provider publishes the file, fetched when the file is missing or older than FILE_MAX_AGE; None
            reads only the file in the data directory.
    """

    file_name: str
    parse: Callable[[pathlib.Path], pd.DataFrame]
    provider_id: str
    url: str | None = None


# The local datasets, keyed by dataset ID.
LOCAL_DATASETS: Mapping[str, LocalDataset] = MappingProxyType(
    {
        "silso_sunspot_daily": LocalDataset(
            "SN_d_tot_V2.0.csv", parse_silso_daily, SILSO_PROVIDER_ID, "https://www.sidc.be/SILSO/INFO/sndtotcsv.php"
        ),
        "silso_sunspot_monthly": LocalDataset(
            "SN_m_tot_V2.0.csv", parse_silso_monthly, SILSO_PROVIDER_ID, "https://www.sidc.be/SILSO/INFO/snmtotcsv.php"
        ),
        "silso_sunspot_smoothed": LocalDataset(
            "SN_ms_tot_V2.0.csv", parse_silso_monthly, SILSO_PROVIDER_ID, "https://www.sidc.be/SILSO/INFO/snmstotcsv.php"
        ),
        "cme_catalog": LocalDataset(
            "cme_catalog.txt", parse_cme_catalog, CME_PROVIDER_ID, "https://cdaw.gsfc.nasa.gov/CME_list/UNIVERSAL_ver2/text_ver/univ_all.txt"
        ),
    }
)


class LocalPipeline:
    """Ingests the datasets of complete files in the data directory, each file as one chunk.

    The files hold the whole dataset, so the requested time range is not used and every run replaces the stored documents.
    A file with download URL is fetched first when it is missing or older than FILE_MAX_AGE.
    """

    def __init__(
        self,
        *,
        timeseries: TimeSeriesRepository,
        coverage_repository: CoverageRepository,
        metadata_repository: MetadataRepository,
        data_dir: pathlib.Path,
        local_datasets: Mapping[str, LocalDataset] = LOCAL_DATASETS,
    ) -> None:
        """Initializes the local pipeline.

        Args:
            timeseries: The repository storing the data documents.
            coverage_repository: The repository storing which time ranges are stored.
            metadata_repository: The repository storing the metadata records.
            data_dir: The directory holding the dataset files.
            local_datasets: The local datasets by dataset ID.
        """
        self.timeseries = timeseries
        self.coverage_repository = coverage_repository
        self.metadata_repository = metadata_repository
        self.data_dir = data_dir
        self.local_datasets = local_datasets
        self.session = requests.Session()

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Replaces the stored documents of each local dataset with the content of its file.

        Args:
            datasets: The IDs of local datasets, keys of the local datasets of the pipeline.
            start_time: Not used, as each file holds the whole dataset.
            end_time: Not used, as each file holds the whole dataset.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with one chunk per dataset and the datasets whose file could not be ingested.

        Raises:
            ValueError: If no dataset is given or a dataset ID is no local dataset.
            FileNotFoundError: If the file of a dataset without download URL does not exist.
        """
        del start_time, end_time
        if not datasets:
            raise ValueError("The local pipeline needs at least one dataset.")
        unknown_datasets = sorted(set(datasets) - self.local_datasets.keys())
        if unknown_datasets:
            raise ValueError(f"Unknown local datasets {unknown_datasets}; known are {sorted(self.local_datasets)}.")

        report = IngestionReport()
        for dataset_index, dataset_id in enumerate(datasets):
            collection = sanitize_collection_name(dataset_id)
            ingest = partial(self._replace_with_file, self.local_datasets[dataset_id], collection)
            if ingest_or_record_gap(report, dataset_id, WHOLE_FILE_START, WHOLE_FILE_STOP, ingest) is not None:
                refresh_coverage(self.coverage_repository, dataset_id, collection)
            if progress_callback:
                progress_callback((dataset_index + 1) / len(datasets), f"Processed local file of {dataset_id}")
        return report

    def _replace_with_file(self, dataset: LocalDataset, collection: str) -> int:
        """Makes the stored documents of a dataset equal the documents built from its whole file, writing only the differences.

        The file is read completely before anything is written, so an unreadable file keeps the stored documents.

        Args:
            dataset: The local dataset.
            collection: The collection holding the data documents.

        Returns:
            The number of documents built from the file.

        Raises:
            FileNotFoundError: If the file does not exist and has no download URL.
            DataRetrievalError: If the file is missing and its download fails.
            DataProcessingError: If the file cannot be read or holds no valid row.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        path = self.data_dir / dataset.file_name
        self._refresh_file(dataset, path)
        frame = dataset.parse(path)
        documents = frame_to_documents(frame, source_identifier(SourceType.LOCAL, dataset.provider_id))
        self.timeseries.ensure_collection(collection, LOCAL_GRANULARITY)
        changes = self.timeseries.replace_range(collection, WHOLE_FILE_START, WHOLE_FILE_STOP, documents)
        logger.debug(
            "Synchronized %s with its file of %d documents: %d inserted, %d deleted", collection, len(documents), changes.inserted, changes.deleted
        )
        self._save_metadata(collection, [str(column) for column in frame.columns])
        return len(documents)

    def _refresh_file(self, dataset: LocalDataset, path: pathlib.Path) -> None:
        """Fetches the file of a dataset with download URL when it is missing or older than FILE_MAX_AGE.

        A failed download of an existing file is logged and the previous file is read instead.

        Args:
            dataset: The local dataset.
            path: The path of its file in the data directory.

        Raises:
            DataRetrievalError: If the file is missing and its download fails.
        """
        if dataset.url is None:
            return
        if path.exists() and time.time() - path.stat().st_mtime < FILE_MAX_AGE.total_seconds():
            return
        try:
            download_file(self.session, dataset.url, path, DOWNLOAD_TIMEOUT, f"File {dataset.file_name}")
        except DataRetrievalError:
            if not path.exists():
                raise
            logger.warning("Fetching a newer %s failed, the previous file is read", dataset.file_name, exc_info=True)
            return
        logger.info("Fetched %s from %s", dataset.file_name, dataset.url)

    def _save_metadata(self, collection: str, fields: list[str]) -> None:
        """Saves the metadata record of a local dataset: its fields, described by the curated metadata laid over it.

        A failing save is logged, as the stored data documents stay valid without metadata.

        Args:
            collection: The collection holding the data documents, named like the dataset.
            fields: The fields of the stored documents.
        """
        try:
            record = build_minimal_metadata(collection, collection, SourceType.LOCAL, fields, None, datetime.now(UTC))
            save_with_overlays(self.metadata_repository, [record])
        except METADATA_SAVE_ERRORS:
            logger.exception("Failed to save metadata of %s", collection)
