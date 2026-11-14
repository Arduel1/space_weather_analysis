import logging
import pathlib
from datetime import UTC, datetime, timedelta

import pandas as pd
import requests

from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.chunks import (
    CHUNK_ERRORS,
    refresh_coverage,
    replace_all_documents,
    require_single_dataset,
    run_single_dataset,
    save_minimal_metadata,
)
from pipeline_service.pipelines.report import IngestionReport
from pipeline_service.pipelines.timestamped_files import MEDIA_KEEP, name_times, remove_files_before, timestamped_name
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.gong import GONG_PAGE_URL, download_map, recent_maps
from space_weather_analysis.clients.hmi import HMI_SYNOPTIC_URL, latest_maps
from space_weather_analysis.clients.image_sequences import FRAME_FILE_FIELD
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.solar.magnetic_field import compute_field
from space_weather_analysis.solar.synoptic_maps import Magnetogram, gong_synoptic_map, hmi_synoptic_map
from space_weather_analysis.timeseries.documents import TimeSeriesDocument, frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.atomic_files import write_text_atomic
from space_weather_analysis.util.error_handling import DataRetrievalError
from space_weather_analysis.util.naming import sanitize_collection_name

logger = logging.getLogger(__name__)

# The one dataset holding the computed models of the coronal magnetic field, one document per model naming its file
PFSS_DATASET = "gong_pfss"
# Models lie hours apart
PFSS_GRANULARITY = Granularity.HOURS
# Span of the maps modelled, the span of the animation
MODEL_SPAN = timedelta(days=1)
# Most models computed in one run, newest first, so that a run stays short while the models of a day are filled in
MODELS_PER_RUN = 4
# Failures of reading a map or computing its model, which leave the run without a new model
MODEL_ERRORS = (OSError, ValueError)
# Extension of the stored model files
MODEL_EXTENSION = ".json"


def model_file_name(time: datetime) -> str:
    """Names the stored file of a model by the UTC time of its map."""
    return timestamped_name(time, MODEL_EXTENSION)


class PfssPipeline:
    """Computes the PFSS models of the coronal magnetic field from synoptic magnetograms, one file each.

    Each run lists the GONG maps of the last day and computes the models not stored yet, newest first and at most
    MODELS_PER_RUN. Without GONG maps, as on hosts the GONG server does not answer, it models the daily updated HMI map
    instead. Models and maps older than MEDIA_KEEP are removed, except the newest model, which the dashboard shows.
    """

    def __init__(
        self,
        *,
        timeseries: TimeSeriesRepository,
        coverage_repository: CoverageRepository,
        metadata_repository: MetadataRepository,
        media_dir: pathlib.Path,
        map_dir: pathlib.Path,
    ) -> None:
        """Initializes the PFSS pipeline.

        Args:
            timeseries: The repository storing one document per model.
            coverage_repository: The repository storing which time ranges are stored.
            metadata_repository: The repository storing the metadata records.
            media_dir: The directory holding one directory of files per dataset, served with the dashboard.
            map_dir: The directory the downloaded maps are kept in.
        """
        self.timeseries = timeseries
        self.coverage_repository = coverage_repository
        self.metadata_repository = metadata_repository
        self.media_dir = media_dir
        self.map_dir = map_dir
        self.session = requests.Session()

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Stores the models of the synoptic maps of the last day.

        Args:
            datasets: The dataset IDs; only PFSS_DATASET is known.
            start_time: Not used, as only the newest map is modelled.
            end_time: Not used, as only the newest map is modelled.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with one chunk for the newest map, a gap if no model could be stored.

        Raises:
            ValueError: If a dataset other than PFSS_DATASET is given.
        """
        del start_time, end_time
        require_single_dataset(datasets, PFSS_DATASET, "PFSS")
        now = datetime.now(UTC)
        return run_single_dataset(
            PFSS_DATASET,
            (now - MEDIA_KEEP, now),
            lambda: self._store_models(now),
            what="PFSS model",
            errors=(*CHUNK_ERRORS, *MODEL_ERRORS),
            progress_callback=progress_callback,
        )

    def _store_models(self, now: datetime) -> None:
        """Computes the missing models, removes the old ones and writes one document per stored model.

        Raises:
            SourceUnreachableError: If neither GONG nor JSOC sent an answer.
            DataRetrievalError: If no map can be found or downloaded.
            OSError: If a map cannot be read or a model file cannot be written.
            ValueError: If a map is no synoptic map the model accepts.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        directory = self.media_dir / PFSS_DATASET
        try:
            gong_maps = recent_maps(self.session, now, MODEL_SPAN)
        except DataRetrievalError as error:
            logger.warning("GONG maps not available, the model uses the HMI synoptic map: %s", error)
            gong_maps = []
        if gong_maps:
            self._store_gong_models(directory, gong_maps)
            source_url = GONG_PAGE_URL
        else:
            self._store_hmi_model(directory, now)
            source_url = HMI_SYNOPTIC_URL
        self._prune(directory, now - MEDIA_KEEP)
        collection = sanitize_collection_name(PFSS_DATASET)
        replace_all_documents(self.timeseries, collection, PFSS_GRANULARITY, self._documents(directory))
        refresh_coverage(self.coverage_repository, PFSS_DATASET, collection)
        self._save_metadata(collection, source_url)

    def _store_gong_models(self, directory: pathlib.Path, gong_maps: list[tuple[str, datetime]]) -> None:
        """Computes the models of the GONG maps not modelled yet, newest first and at most MODELS_PER_RUN.

        Raises:
            DataRetrievalError: If a map cannot be downloaded.
            OSError: If a map cannot be read or a model file cannot be written.
            ValueError: If a map is no synoptic map the model accepts.
        """
        missing = [(url, time) for url, time in gong_maps if not (directory / model_file_name(time)).exists()]
        # The maps are listed newest first, so a run fills in the newest models before older ones
        for url, map_time in missing[:MODELS_PER_RUN]:
            model = compute_field(gong_synoptic_map(download_map(self.session, url, self.map_dir)), Magnetogram.GONG)
            self._write_model(directory, map_time, model.model_dump_json())
            logger.info("Computed the PFSS model of the GONG map of %s: %d field lines", map_time, len(model.lines))

    def _store_hmi_model(self, directory: pathlib.Path, now: datetime) -> None:
        """Computes the model of the newest HMI map unless it is modelled already.

        Raises:
            SourceUnreachableError: If JSOC sent no answer.
            DataRetrievalError: If no map can be found or downloaded.
            OSError: If a map cannot be read or the model file cannot be written.
            ValueError: If the maps do not share their grid or the model does not accept the map.
        """
        current, final, updated = latest_maps(self.session, now, self.map_dir)
        if (directory / model_file_name(updated)).exists():
            return
        model = compute_field(hmi_synoptic_map(current, final, updated), Magnetogram.HMI)
        self._write_model(directory, updated, model.model_dump_json())
        logger.info("Computed the PFSS model of the HMI map of %s: %d field lines", updated, len(model.lines))

    @staticmethod
    def _write_model(directory: pathlib.Path, map_time: datetime, content: str) -> None:
        """Stores a model file named by the time of its map."""
        directory.mkdir(parents=True, exist_ok=True)
        write_text_atomic(directory / model_file_name(map_time), content)

    def _prune(self, directory: pathlib.Path, cutoff: datetime) -> None:
        """Deletes the stored models and downloaded maps older than the cutoff, but never the newest model."""
        model_times = name_times(sorted(path.name for path in directory.glob(f"*{MODEL_EXTENSION}")), MODEL_EXTENSION)
        # A daily HMI map may be older than the cutoff; its model stays until a newer one replaces it
        newest = model_times.max().to_pydatetime() if len(model_times) else cutoff
        remove_files_before(directory, MODEL_EXTENSION, min(cutoff, newest))
        # GONG maps are gzipped FITS files, HMI maps plain ones
        for path in [*self.map_dir.glob("*.fits.gz"), *self.map_dir.glob("*.fits")]:
            if datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
                path.unlink()

    def _documents(self, directory: pathlib.Path) -> list[TimeSeriesDocument]:
        """Builds one document per stored model, at the time of its map."""
        names = sorted(path.name for path in directory.glob(f"*{MODEL_EXTENSION}"))
        return frame_to_documents(
            pd.DataFrame({FRAME_FILE_FIELD: names}, index=name_times(names, MODEL_EXTENSION)), source_identifier(SourceType.GONG, PFSS_DATASET)
        )

    def _save_metadata(self, collection: str, source_url: str) -> None:
        """Saves the metadata record of the dataset naming the source of this run; a failing save is logged."""
        save_minimal_metadata(self.metadata_repository, PFSS_DATASET, collection, SourceType.GONG, [FRAME_FILE_FIELD], source_url)
