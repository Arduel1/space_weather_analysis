import json
import logging
import pathlib
from datetime import UTC, datetime
from typing import Any

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
from space_weather_analysis.clients.image_sequences import FRAME_FILE_FIELD
from space_weather_analysis.clients.ovation import OVATION_PAGE_URL, AuroraGrid, fetch_grid
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.geospace.aurora import night_side_edge, subsolar_point
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.timeseries.documents import TimeSeriesDocument, frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.atomic_files import write_text_atomic
from space_weather_analysis.util.naming import sanitize_collection_name

logger = logging.getLogger(__name__)

# The one dataset holding the stored OVATION maps, one document per map naming its file
OVATION_DATASET = "swpc_ovation_grid"
# Maps lie minutes apart
OVATION_GRANULARITY = Granularity.MINUTES
# Cells below this probability in percent are left out of a stored map, which keeps the files small; the model calls
# lower values no visible aurora
STORED_PROBABILITY = 5
# Probability in percent from which a cell counts as aurora for the edge of the oval
EDGE_PROBABILITY = 10.0
# Fields of a map document besides its file: the edge of the oval on the night side of each hemisphere in geomagnetic
# latitude, the place where the Sun stands overhead, and the time of the solar wind measurement
EDGE_NORTH_FIELD = "edge_north"
EDGE_SOUTH_FIELD = "edge_south"
SUBSOLAR_LATITUDE_FIELD = "subsolar_latitude"
SUBSOLAR_LONGITUDE_FIELD = "subsolar_longitude"
OBSERVATION_FIELD = "observation_time"
MAP_FIELDS = (FRAME_FILE_FIELD, EDGE_NORTH_FIELD, EDGE_SOUTH_FIELD, SUBSOLAR_LATITUDE_FIELD, SUBSOLAR_LONGITUDE_FIELD, OBSERVATION_FIELD)
# Extension of the stored map files
MAP_EXTENSION = ".json"


def map_file_name(time: datetime) -> str:
    """Names the stored file of a map by the UTC time it is valid for."""
    return timestamped_name(time, MAP_EXTENSION)


def map_summary(grid: AuroraGrid) -> dict[str, object]:
    """Describes a map by the edges of its oval, the subsolar point and the time of its measurement."""
    subsolar_latitude, subsolar_longitude = subsolar_point(grid.forecast_time)
    # The subsolar longitude sets the local time, which tells the night side where the edge is measured
    edges = {
        field: night_side_edge(grid.latitude, grid.longitude, grid.probability, subsolar_longitude, EDGE_PROBABILITY, north)
        for field, north in ((EDGE_NORTH_FIELD, True), (EDGE_SOUTH_FIELD, False))
    }
    return {
        **{field: None if edge is None else round(edge, 2) for field, edge in edges.items()},
        SUBSOLAR_LATITUDE_FIELD: round(subsolar_latitude, 3),
        SUBSOLAR_LONGITUDE_FIELD: round(subsolar_longitude, 3),
        OBSERVATION_FIELD: grid.observation_time.isoformat(),
    }


def map_content(grid: AuroraGrid) -> dict[str, object]:
    """Builds the stored file of a map: its summary and the cells of at least STORED_PROBABILITY as parallel lists."""
    kept = grid.probability >= STORED_PROBABILITY
    return {
        **map_summary(grid),
        "longitude": grid.longitude[kept].astype(int).tolist(),
        "latitude": grid.latitude[kept].astype(int).tolist(),
        "probability": grid.probability[kept].astype(int).tolist(),
    }


def _read_summary(path: pathlib.Path) -> dict[str, Any] | None:
    """Reads the summary stored in a map file; None, logged, if the file cannot be read or holds no JSON object."""
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("OVATION map file %s not readable: %s", path.name, error)
        return None
    if not isinstance(summary, dict):
        logger.warning("OVATION map file %s holds no JSON object", path.name)
        return None
    return summary


class OvationPipeline:
    """Stores the newest map of the OVATION aurora model on every run, one file per map, and keeps the maps of a day.

    SWPC publishes only the newest map, so the day of maps is collected run by run.
    """

    def __init__(
        self,
        *,
        timeseries: TimeSeriesRepository,
        coverage_repository: CoverageRepository,
        metadata_repository: MetadataRepository,
        media_dir: pathlib.Path,
    ) -> None:
        """Initializes the OVATION pipeline.

        Args:
            timeseries: The repository storing one document per map.
            coverage_repository: The repository storing which time ranges are stored.
            metadata_repository: The repository storing the metadata records.
            media_dir: The directory holding one directory of files per dataset, served with the dashboard.
        """
        self.timeseries = timeseries
        self.coverage_repository = coverage_repository
        self.metadata_repository = metadata_repository
        self.media_dir = media_dir
        self.session = requests.Session()

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Stores the newest map and removes the maps older than MEDIA_KEEP.

        Args:
            datasets: The dataset IDs; only OVATION_DATASET is known.
            start_time: Not used, as SWPC publishes only the newest map.
            end_time: Not used, as SWPC publishes only the newest map.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with one chunk for the newest map, a gap if it could not be stored.

        Raises:
            ValueError: If a dataset other than OVATION_DATASET is given.
        """
        del start_time, end_time
        require_single_dataset(datasets, OVATION_DATASET, "OVATION")
        now = datetime.now(UTC)
        return run_single_dataset(
            OVATION_DATASET,
            (now - MEDIA_KEEP, now),
            lambda: self._store_newest_map(now),
            what="OVATION map",
            # A failed write of a map file leaves a gap like a failed download
            errors=(*CHUNK_ERRORS, OSError),
            progress_callback=progress_callback,
        )

    def _store_newest_map(self, now: datetime) -> None:
        """Stores the newest map unless stored already, removes the maps older than MEDIA_KEEP and writes one document per map.

        Raises:
            DataRetrievalError: If the map cannot be downloaded.
            DataProcessingError: If the map cannot be read.
            OSError: If a map file cannot be written or removed.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        grid = fetch_grid(self.session)
        directory = self.media_dir / OVATION_DATASET
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / map_file_name(grid.forecast_time)
        # SWPC refreshes the map every few minutes; a map valid for the same time is written once
        if not path.exists():
            write_text_atomic(path, json.dumps(map_content(grid), separators=(",", ":")))
        remove_files_before(directory, MAP_EXTENSION, now - MEDIA_KEEP)
        collection = sanitize_collection_name(OVATION_DATASET)
        # The documents are rebuilt from the kept files, so they always name exactly the files on disk
        replace_all_documents(self.timeseries, collection, OVATION_GRANULARITY, self._documents(directory))
        refresh_coverage(self.coverage_repository, OVATION_DATASET, collection)
        self._save_metadata(collection)
        logger.info("Stored the OVATION map valid at %s", grid.forecast_time)

    def _documents(self, directory: pathlib.Path) -> list[TimeSeriesDocument]:
        """Builds one document per stored map from the summary in its file, at the time the map is valid for.

        A file that cannot be read as a summary is logged and left out, so the other maps keep their documents.
        """
        names: list[str] = []
        rows: list[dict[str, Any]] = []
        for path in sorted(directory.glob(f"*{MAP_EXTENSION}")):
            summary = _read_summary(path)
            if summary is None:
                continue
            names.append(path.name)
            rows.append({FRAME_FILE_FIELD: path.name, **{field: summary.get(field) for field in MAP_FIELDS[1:]}})
        times = name_times(names, MAP_EXTENSION)
        return frame_to_documents(pd.DataFrame(rows, index=times, columns=list(MAP_FIELDS)), source_identifier(SourceType.OVATION, OVATION_DATASET))

    def _save_metadata(self, collection: str) -> None:
        """Saves the metadata record of the dataset; a failing save is logged, as the documents stay valid without it."""
        save_minimal_metadata(self.metadata_repository, OVATION_DATASET, collection, SourceType.OVATION, MAP_FIELDS, OVATION_PAGE_URL)
