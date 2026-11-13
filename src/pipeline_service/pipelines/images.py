import logging
import pathlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import requests

from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.chunks import (
    CHUNK_ERRORS,
    refresh_coverage,
    replace_all_documents,
    require_known_datasets,
    resolve_time_range,
    run_per_dataset,
    save_minimal_metadata,
)
from pipeline_service.pipelines.image_frames import (
    difference_file_name,
    disk_columns,
    frame_file_name,
    model_frame_file_name,
    remove_files_except,
    store_differences,
    store_frame,
    zoom_frame_file_name,
)
from pipeline_service.pipelines.report import IngestionReport
from pipeline_service.pipelines.timestamped_files import remove_files_before
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.donki import BEGIN_FIELD, END_FIELD, EVENT_TYPE_FIELD, LABEL_FIELD, LINK_FIELD, LOCATION_FIELD
from space_weather_analysis.clients.image_sequences import (
    DIFFERENCE_FILE_FIELD,
    FRAME_FILE_FIELD,
    HELIOVIEWER_SCREENSHOT_URL,
    SOLAR_RADIUS_ARCSEC,
    ZOOM_EVENT_FIELDS,
    EventZoom,
    Frame,
    ImageProduct,
    configured_image_products,
    fetch_frames,
    thin_frames,
)
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.solar.geometry import disk_position, location_coordinates, solar_b0
from space_weather_analysis.timeseries.documents import frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.error_handling import DataProcessingError, DataRetrievalError
from space_weather_analysis.util.naming import sanitize_collection_name

logger = logging.getLogger(__name__)

# Frames lie minutes apart
IMAGE_GRANULARITY = Granularity.MINUTES
# Range of a run without start time, the span the frame lists of the providers hold
DEFAULT_LOOKBACK = timedelta(days=1)
# Age after which the metadata record of a dataset is saved again
METADATA_REFRESH = timedelta(hours=1)
# Failures of fetching or converting a single frame, which leave the frame out of the run
FRAME_ERRORS = (DataRetrievalError, DataProcessingError, OSError)
# A difference image of a zoom compares a frame with the one about this much earlier
DIFFERENCE_SPAN = timedelta(minutes=2)


def _text_or_none(value: object) -> str | None:
    """Returns an event field as text; None for a missing or empty value, which the stored document then leaves out."""
    return str(value) if value else None


class ImagePipeline:
    """Keeps a scaled copy of the images of each image sequence, and one document per kept frame naming its file.

    Each run reads the frame list of a dataset, keeps one frame per spacing of its product, fetches only the frames not
    stored yet, scales them down and removes the frames older than the time its product keeps them.
    """

    def __init__(
        self,
        *,
        timeseries: TimeSeriesRepository,
        coverage_repository: CoverageRepository,
        metadata_repository: MetadataRepository,
        media_dir: pathlib.Path,
        products: Mapping[str, ImageProduct] | None = None,
    ) -> None:
        """Initializes the image pipeline.

        Args:
            timeseries: The repository storing the frame documents.
            coverage_repository: The repository storing which time ranges are stored.
            metadata_repository: The repository storing the metadata records.
            media_dir: The directory holding one directory of frames per dataset.
            products: The image products by dataset ID; None reads the products file of the settings.

        Raises:
            OSError: If the products file of the settings cannot be read.
            tomllib.TOMLDecodeError: If the products file is no valid TOML.
            pydantic.ValidationError: If a product is invalid.
        """
        self.timeseries = timeseries
        self.coverage_repository = coverage_repository
        self.metadata_repository = metadata_repository
        self.media_dir = media_dir
        self.products: Mapping[str, ImageProduct] = products if products is not None else configured_image_products()
        self.session = requests.Session()

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Stores the frames of each image sequence in the time range, as far back as the frame lists reach.

        Once a provider sent no answer, the remaining datasets of the run are reported as gaps without a request.

        Args:
            datasets: The IDs of image datasets, keys of the products.
            start_time: The start of the time range (inclusive); None starts DEFAULT_LOOKBACK before the end.
            end_time: The end of the time range (exclusive); None ends now.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with one chunk per dataset and the datasets that could not be ingested.

        Raises:
            ValueError: If no dataset is given or a dataset ID is no image dataset.
        """
        require_known_datasets(datasets, self.products, "image")
        start, stop = resolve_time_range(start_time, end_time, DEFAULT_LOOKBACK)

        return run_per_dataset(
            datasets,
            (start, stop),
            lambda dataset_id: self._ingest(dataset_id, self.products[dataset_id], start, stop),
            provider="Image provider",
            dataset_kind="image",
            # A failed file operation on the stored frames leaves this dataset a gap, like a failed download
            errors=(*CHUNK_ERRORS, OSError),
            progress_callback=progress_callback,
        )

    def _ingest(self, dataset_id: str, product: ImageProduct, start: datetime, stop: datetime) -> None:
        """Stores the missing frames of a dataset in the range, removes the frames past their keep time and writes the frame documents.

        A frame that cannot be fetched or read is logged and left out; it is tried again on the next run.

        Args:
            dataset_id: The dataset ID.
            product: The image product of the dataset.
            start: The start of the time range (inclusive).
            stop: The end of the time range (exclusive).

        Raises:
            SourceUnreachableError: If the provider sent no answer for the frame list.
            DataRetrievalError: If the frame list cannot be downloaded.
            DataProcessingError: If the frame list cannot be read.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        directory = self.media_dir / dataset_id
        directory.mkdir(parents=True, exist_ok=True)
        # A zoom and a model run replace all their frames at once and do not follow the time range
        if product.zoom is not None:
            self._ingest_zoom(dataset_id, product, product.zoom, directory)
            return
        if product.model_run:
            self._ingest_model_run(dataset_id, product, directory)
            return
        frames_in_range = [frame for frame in fetch_frames(self.session, product) if start <= frame.time < stop]
        frames = thin_frames(frames_in_range, product.spacing)
        fetched = self._store_missing_frames(dataset_id, directory, {frame_file_name(frame.time): frame for frame in frames}, product.size)
        # Frames past the keep time of the product lose their file and their document
        cutoff = datetime.now(UTC) - product.keep
        remove_files_before(directory, ".jpg", cutoff)
        collection = sanitize_collection_name(dataset_id)
        self.timeseries.ensure_collection(collection, IMAGE_GRANULARITY)
        self.timeseries.replace_range(collection, datetime.min.replace(tzinfo=UTC), cutoff, [])
        # Only frames with a file get a document; a frame whose download failed is tried again on the next run
        stored = [frame for frame in frames if (directory / frame_file_name(frame.time)).exists()]
        if stored:
            names = [frame_file_name(frame.time) for frame in stored]
            columns: dict[str, list[object]] = {FRAME_FILE_FIELD: list(names)}
            if product.solar_disk:
                columns.update(disk_columns(product, [directory / name for name in names], [frame.time for frame in stored]))
            # The documents from the first stored frame to the end of the range are replaced; older kept ones stay
            documents = frame_to_documents(
                pd.DataFrame(columns, index=pd.DatetimeIndex([frame.time for frame in stored])),
                source_identifier(SourceType.IMAGES, dataset_id),
            )
            self.timeseries.replace_range(collection, stored[0].time, stop, documents)
            refresh_coverage(self.coverage_repository, dataset_id, collection)
            self._save_metadata(dataset_id, collection, product.list_url)
        logger.info("Image dataset %s: %d frames in the list range, %d fetched", dataset_id, len(frames), fetched)

    def _ingest_zoom(self, dataset_id: str, product: ImageProduct, zoom: EventZoom, directory: pathlib.Path) -> None:
        """Stores the frames around the place of the newest event over the whole event, replacing those of earlier events.

        Args:
            dataset_id: The dataset ID.
            product: The image product of the dataset, a Helioviewer product.
            zoom: The event zoom of the product.
            directory: The directory holding the frames of the dataset.

        Raises:
            pymongo.errors.PyMongoError: If the documents cannot be read or stored.
        """
        source = product.helioviewer
        # The newest stored event of the followed type, at its peak time
        latest = self.timeseries.read_latest(sanitize_collection_name(zoom.events), data_values={EVENT_TYPE_FIELD: zoom.event_type})
        if source is None or latest is None:
            return
        peak, event = latest
        # The place on the Sun as heliographic latitude and longitude, from the location text such as S15W53
        coordinates = location_coordinates(str(event.get(LOCATION_FIELD) or ""))
        begin = pd.to_datetime(str(event.get(BEGIN_FIELD) or ""), utc=True, errors="coerce")
        end = pd.to_datetime(str(event.get(END_FIELD) or ""), utc=True, errors="coerce")
        if coordinates is None or pd.isna(begin) or pd.isna(end):
            logger.info("The newest %s event of %s has no place, begin or end; no zoom", zoom.event_type, peak)
            return
        # The place projected onto the disk seen from Earth at the peak; an event behind the limb gets no zoom
        west, north, visible = disk_position(np.array([coordinates[0]]), np.array([coordinates[1]]), solar_b0(pd.DatetimeIndex([peak])))
        if not visible[0]:
            return
        # Helioviewer centres an image on a point given in arcseconds from the disk centre
        centre = (float(west[0]) * SOLAR_RADIUS_ARCSEC, float(north[0]) * SOLAR_RADIUS_ARCSEC)
        # The frames spread evenly over the event and its buffers, but not closer than the cadence of the instrument
        first, last = begin.to_pydatetime() - zoom.buffer, end.to_pydatetime() + zoom.buffer
        step = max((last - first) / (zoom.frames - 1), zoom.shortest_step)
        times = [first + step * index for index in range(int((last - first) / step) + 1)]
        wanted = {zoom_frame_file_name(time, source.image_scale, centre): Frame(time, source.url(time, product.size, centre)) for time in times}
        # Only the frames of the newest event and their difference images stay
        remove_files_except(directory, set(wanted) | {difference_file_name(name) for name in wanted})
        self._store_missing_frames(dataset_id, directory, wanted, product.size)
        stored = [(name, frame.time) for name, frame in wanted.items() if (directory / name).exists()]
        # Each difference image compares with the frame about DIFFERENCE_SPAN earlier, at least the previous one
        differences = store_differences(directory, [name for name, _ in stored], max(1, round(DIFFERENCE_SPAN / step)))
        frame_table = pd.DataFrame(
            {
                FRAME_FILE_FIELD: [name for name, _ in stored],
                DIFFERENCE_FILE_FIELD: differences,
                ZOOM_EVENT_FIELDS[0]: _text_or_none(event.get(LABEL_FIELD)),
                ZOOM_EVENT_FIELDS[1]: _text_or_none(event.get(LOCATION_FIELD)),
                ZOOM_EVENT_FIELDS[2]: peak.isoformat(),
                ZOOM_EVENT_FIELDS[3]: _text_or_none(event.get(LINK_FIELD)),
            },
            index=pd.DatetimeIndex([time for _, time in stored]),
        )
        self._replace_all_documents(dataset_id, frame_table, HELIOVIEWER_SCREENSHOT_URL)
        logger.info("Zoom %s on the %s event of %s at %s: %d frames", dataset_id, zoom.event_type, peak, event.get(LOCATION_FIELD), len(stored))

    def _ingest_model_run(self, dataset_id: str, product: ImageProduct, directory: pathlib.Path) -> None:
        """Stores the frames of the newest run of a model, replacing the frames and documents of earlier runs.

        A frame that cannot be fetched is left out and tried again on the next run, while the stored frames of the
        newest run stay.

        Args:
            dataset_id: The dataset ID.
            product: The image product of the dataset.
            directory: The directory holding the frames of the dataset.

        Raises:
            SourceUnreachableError: If the provider sent no answer for the frame list.
            DataRetrievalError: If the frame list cannot be downloaded.
            DataProcessingError: If the frame list cannot be read.
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        frames = thin_frames(fetch_frames(self.session, product), product.spacing)
        if not frames:
            return
        # The file names carry the first frame time of the run, so the files of earlier runs are not wanted and removed
        wanted = {model_frame_file_name(frames[0].time, frame.url): frame for frame in frames}
        remove_files_except(directory, set(wanted))
        self._store_missing_frames(dataset_id, directory, wanted, product.size)
        stored = [(name, frame.time) for name, frame in wanted.items() if (directory / name).exists()]
        frame_table = pd.DataFrame({FRAME_FILE_FIELD: [name for name, _ in stored]}, index=pd.DatetimeIndex([time for _, time in stored]))
        self._replace_all_documents(dataset_id, frame_table, product.list_url)
        logger.info("Model run %s: %d of %d frames stored", dataset_id, len(stored), len(wanted))

    def _store_missing_frames(self, dataset_id: str, directory: pathlib.Path, wanted: Mapping[str, Frame], size: int) -> int:
        """Downloads the wanted frames not stored yet; a frame that fails is logged, left out and tried again on the next run.

        Args:
            dataset_id: The dataset ID.
            directory: The directory holding the frames of the dataset.
            wanted: The frames to store, by file name.
            size: The length of the longer side of a stored frame in pixels.

        Returns:
            The number of frames downloaded.
        """
        fetched = 0
        for name, frame in wanted.items():
            path = directory / name
            if path.exists():
                continue
            try:
                store_frame(self.session, frame, path, size)
            except FRAME_ERRORS as error:
                logger.warning("Frame %s of %s not stored: %s", frame.url, dataset_id, error)
                continue
            fetched += 1
        return fetched

    def _replace_all_documents(self, dataset_id: str, frame_table: pd.DataFrame, list_url: str) -> None:
        """Replaces all frame documents of a dataset; with frames, refreshes its coverage and saves its metadata.

        Args:
            dataset_id: The dataset ID.
            frame_table: The fields of the stored frames, indexed by frame time; empty to remove all documents.
            list_url: The address of the frame list, named as source in the metadata.

        Raises:
            pymongo.errors.PyMongoError: If the documents cannot be stored.
        """
        collection = sanitize_collection_name(dataset_id)
        has_frames = len(frame_table) > 0
        documents = frame_to_documents(frame_table, source_identifier(SourceType.IMAGES, dataset_id)) if has_frames else []
        replace_all_documents(self.timeseries, collection, IMAGE_GRANULARITY, documents)
        if has_frames:
            refresh_coverage(self.coverage_repository, dataset_id, collection)
            self._save_metadata(dataset_id, collection, list_url)

    def _save_metadata(self, dataset_id: str, collection: str, list_url: str) -> None:
        """Saves the metadata record of a dataset unless the stored one is younger than METADATA_REFRESH; a failing save is logged.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the frame documents.
            list_url: The address of the frame list, named as source.
        """
        save_minimal_metadata(
            self.metadata_repository, dataset_id, collection, SourceType.IMAGES, [FRAME_FILE_FIELD], list_url, refresh_after=METADATA_REFRESH
        )
