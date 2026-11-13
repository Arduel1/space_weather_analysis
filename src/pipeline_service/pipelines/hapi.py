import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

from pipeline_service.chunk_status.repository import ChunkStatusRepository
from pipeline_service.parsing.hapi import convert_hapi_to_frame
from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.chunks import METADATA_SAVE_ERRORS, chunk_unit_for_cadence, run_chunked, settled, stored_cadence
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import CatalogEntryDict, CatalogMap, SourceType
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.clients.hapi import HapiClient
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.models import MAX_ARRAY_COLUMNS, MetadataSourceKind
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.timeseries.documents import frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity, TimeBucket
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.error_handling import DataProcessingError
from space_weather_analysis.util.hapi_parameters import parameters_too_large_for_columns
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import ensure_aware, parse_iso_duration, parse_iso_time
from space_weather_analysis.util.types import TimeRange

logger = logging.getLogger(__name__)

# Keys hapiclient adds to the /info response to describe its own request, cache files and timing.
HAPICLIENT_REQUEST_KEYS = frozenset(
    {
        "x_server",
        "x_dataset",
        "x_parameters",
        "x_time.min",
        "x_time.max",
        "x_requestDate",
        "x_cacheDir",
        "x_downloadTime",
        "x_readTime",
        "x_metaFileParsed",
        "x_dataFileParsed",
        "x_metaFile",
        "x_dataFile",
        "x_totalTime",
    }
)


@dataclass(frozen=True)
class FetchedInfo:
    """The /info response a server sent with the data of a chunk, the source of the dataset metadata.

    Attributes:
        info: The /info response as returned by hapiclient.
        server_url: The server that sent it.
        retrieved_at: When it was read.
    """

    info: dict[str, Any]
    server_url: str
    retrieved_at: datetime


class HapiPipeline:
    """Ingests HAPI datasets chunk by chunk into MongoDB time series collections.

    The client receives the HAPI sources of every dataset from the master catalog at the start of each run.
    """

    def __init__(
        self,
        *,
        timeseries: TimeSeriesRepository,
        catalog_repository: CatalogRepository,
        metadata_repository: MetadataRepository,
        coverage_repository: CoverageRepository,
        chunk_status: ChunkStatusRepository,
        client_factory: Callable[[CatalogMap], HapiClient] = HapiClient,
        chunk_unit: TimeBucket | None = None,
    ) -> None:
        """Initializes the pipeline with its repositories.

        Args:
            timeseries: The repository storing the data documents.
            catalog_repository: The repository of the master catalog, which lists the HAPI sources.
            metadata_repository: The repository storing the metadata records.
            coverage_repository: The repository storing which time ranges are stored.
            chunk_status: The repository storing the status of each fetched chunk.
            client_factory: Builds the client from the HAPI sources of the master catalog at the start of each run.
            chunk_unit: The calendar unit of the chunks; None selects it per dataset from the cadence in its metadata record.
        """
        self.timeseries = timeseries
        self.catalog_repository = catalog_repository
        self.metadata_repository = metadata_repository
        self.coverage_repository = coverage_repository
        self.chunk_status = chunk_status
        self.client_factory = client_factory
        self.chunk_unit = chunk_unit

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Ingests each dataset in chunks of a calendar grid, skipping chunks whose last fetch is current.

        The time range of every dataset is resolved before the first chunk, so an invalid request stores nothing.

        Args:
            datasets: The IDs of the datasets to ingest.
            start_time: The start of the time range; None takes the earliest catalog start of the dataset.
            end_time: The end of the time range; None takes the latest catalog stop, or now for an ongoing source.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with the number of tried chunks and the chunks that failed.

        Raises:
            ValueError: If no dataset is given, a dataset has no HAPI source in the master catalog, or its time range cannot be resolved.
        """
        if not datasets:
            raise ValueError("The HAPI pipeline needs at least one dataset.")
        client = self.client_factory(self.catalog_repository.get_source_map(SourceType.HAPI))
        time_ranges = {dataset_id: _resolve_time_range(client, dataset_id, start_time, end_time) for dataset_id in datasets}
        return run_chunked(
            HapiChunkSource(client, self.timeseries, self.metadata_repository, self.chunk_unit),
            time_ranges,
            timeseries=self.timeseries,
            coverage_repository=self.coverage_repository,
            chunk_status=self.chunk_status,
            progress_callback=progress_callback,
        )


class HapiChunkSource:
    """The HAPI steps of a chunked run, keeping the latest /info response of each dataset for its metadata."""

    # HAPI datasets have cadences from seconds to minutes
    granularity = Granularity.SECONDS

    def __init__(
        self, client: HapiClient, timeseries: TimeSeriesRepository, metadata_repository: MetadataRepository, unit: TimeBucket | None
    ) -> None:
        """Initializes the source for one run.

        Args:
            client: The client holding the HAPI sources of the master catalog.
            timeseries: The repository storing the data documents.
            metadata_repository: The repository storing the metadata records.
            unit: The calendar unit of the chunks; None selects it per dataset from its cadence.
        """
        self.client = client
        self.timeseries = timeseries
        self.metadata_repository = metadata_repository
        self.unit = unit
        self._latest_info: dict[str, FetchedInfo] = {}

    def collection_for(self, dataset_id: str) -> str:
        """Names the collection of a dataset after its sanitized ID.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The collection name.
        """
        return sanitize_collection_name(dataset_id)

    def chunk_unit(self, dataset_id: str) -> TimeBucket:
        """Names the calendar cell unit of a dataset, the given unit or the one fitting its cadence.

        The cadence comes from the metadata record of the dataset, else from its catalog sources.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The cell unit.
        """
        if self.unit is not None:
            return self.unit
        return chunk_unit_for_cadence(stored_cadence(self.metadata_repository, dataset_id) or self._catalog_cadence(dataset_id))

    def _catalog_cadence(self, dataset_id: str) -> timedelta | None:
        """Reads the first cadence the catalog sources of a dataset give.

        Args:
            dataset_id: The dataset ID.

        Returns:
            The cadence; None if no source gives a valid one.
        """
        for entry in self.client.catalog_map.get(dataset_id, []):
            cadence_text = entry.get("cadence")
            cadence = parse_iso_duration(cadence_text) if cadence_text else None
            if cadence is not None:
                return cadence
        return None

    def is_stable(self, dataset_id: str, collection: str, start: datetime, stop: datetime, fetched_at: datetime) -> bool:
        """Counts fetched data as stable once the fetched range ended SETTLE_DELAY before the fetch.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
            start: The start of the fetched range (inclusive).
            stop: The end of the fetched range (exclusive).
            fetched_at: When the fetch finished.

        Returns:
            True if the fetched range ended long enough before the fetch.
        """
        del dataset_id, collection, start
        return settled(stop, fetched_at)

    def ingest_chunk(self, dataset_id: str, collection: str, start: datetime, stop: datetime) -> bool:
        """Fetches one chunk and replaces its stored documents with its rows; stored documents stay if no source delivers data.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
            start: The start of the chunk (inclusive).
            stop: The end of the chunk (exclusive).

        Returns:
            True if a source delivered data for the chunk.

        Raises:
            DataRetrievalError: If the request failed.
            DataProcessingError: If the response cannot be converted into columns or names no server.
            pymongo.errors.PyMongoError: If the documents cannot be replaced.
        """
        result = self.client.fetch(dataset_id, start, stop)
        if result.data is None:
            return False
        if result.source_url is None:
            raise DataProcessingError(f"The data of {dataset_id} name no HAPI server.")
        data, info = result.data
        retrieved_at = datetime.now(UTC)

        frame = convert_hapi_to_frame(data, info)
        documents = frame_to_documents(frame, hapi_source_identifier(result.source_url), server_url=result.source_url)
        self.timeseries.replace_range(collection, start, stop, documents)
        self._latest_info[dataset_id] = FetchedInfo(info, result.source_url, retrieved_at)
        return True

    def save_metadata(self, dataset_id: str, collection: str) -> None:
        """Saves the metadata record built from the latest /info response of a dataset and the response itself.

        The raw response is saved without the keys hapiclient adds about its request.
        A failing save is logged, as the stored data documents stay valid without metadata.

        Args:
            dataset_id: The dataset ID.
            collection: The collection holding the data documents.
        """
        fetched_info = self._latest_info.get(dataset_id)
        if fetched_info is None:
            return
        # Reported once per dataset, as every chunk skips the same parameters
        skipped_parameters = parameters_too_large_for_columns(fetched_info.info)
        if skipped_parameters:
            logger.warning("Dataset %s: arrays with more than %d elements are not stored: %s", dataset_id, MAX_ARRAY_COLUMNS, skipped_parameters)

        server_info = {key: value for key, value in fetched_info.info.items() if key not in HAPICLIENT_REQUEST_KEYS}
        try:
            record = build_hapi_metadata(dataset_id, collection, server_info, fetched_info.server_url, fetched_info.retrieved_at)
            self.metadata_repository.save_raw(dataset_id, MetadataSourceKind.HAPI, server_info, fetched_info.server_url, fetched_info.retrieved_at)
            save_with_overlays(self.metadata_repository, [record])
        except METADATA_SAVE_ERRORS:
            logger.exception("Failed to save metadata of %s", dataset_id)
            return
        logger.info("Saved metadata of %s", dataset_id)


def hapi_source_identifier(server_url: str) -> str:
    """Builds the source identifier of the documents a HAPI server delivered.

    Args:
        server_url: The base URL of the HAPI server.

    Returns:
        The identifier with the host name of the server as provider ID, or with the whole URL if it names no host.
    """
    return source_identifier(SourceType.HAPI, urlsplit(server_url).hostname or server_url)


def _resolve_time_range(client: HapiClient, dataset_id: str, start_time: datetime | None, end_time: datetime | None) -> TimeRange:
    """Determines the time range of a dataset, taking each missing bound from its catalog sources.

    The range of an ongoing source ends now.

    Args:
        client: The client holding the HAPI sources of the master catalog.
        dataset_id: The dataset ID.
        start_time: The requested start, or None.
        end_time: The requested end, or None.

    Returns:
        Start and end in UTC.

    Raises:
        ValueError: If the master catalog lists no HAPI source for the dataset, or a missing bound cannot be taken from it.
    """
    entries = client.catalog_map.get(dataset_id)
    if not entries:
        raise ValueError(f"The master catalog lists no HAPI source for dataset '{dataset_id}'.")

    if start_time is not None:
        start = ensure_aware(start_time)
    else:
        catalog_start = min(_parse_catalog_times(entries, "start_time"), default=None)
        if catalog_start is None:
            raise ValueError(f"The start of {dataset_id} is neither requested nor given by its catalog sources.")
        start = catalog_start

    if end_time is not None:
        end = ensure_aware(end_time)
    else:
        end_times = _parse_catalog_times(entries, "end_time")
        # An ongoing source has data up to now, beyond the stop time recorded in the catalog
        if any(entry.get("ongoing") for entry in entries):
            end_times.append(datetime.now(UTC))
        catalog_end = max(end_times, default=None)
        if catalog_end is None:
            raise ValueError(f"The end of {dataset_id} is neither requested nor given by its catalog sources.")
        end = catalog_end
    return start, end


def _parse_catalog_times(entries: list[CatalogEntryDict], time_key: Literal["start_time", "end_time"]) -> list[datetime]:
    """Parses one bound of the time range of each catalog entry.

    Args:
        entries: The catalog entries of a dataset.
        time_key: The key of the bound in the time range, 'start_time' or 'end_time'.

    Returns:
        The parsed times, without the entries whose bound is missing or unparsable.
    """
    parsed_times = []
    for entry in entries:
        time_string = entry.get("time_range", {}).get(time_key)
        parsed_time = parse_iso_time(time_string) if time_string else None
        if parsed_time:
            parsed_times.append(parsed_time)
    return parsed_times
