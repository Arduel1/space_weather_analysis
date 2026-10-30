import logging
import math
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from pymongo.database import Database

from pipeline_service.database.mongo_loader import MongoLoader
from pipeline_service.pipelines.base import BasePipeline, ProgressCallback
from pipeline_service.util.documents import dataframe_to_documents
from pipeline_service.util.time import generate_time_chunks
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.gfz import GfzClient
from space_weather_analysis.coverage.repository import CoverageRepository, MongoCoverageRepository
from space_weather_analysis.metadata.models import CitationInfo, DatasetMetadata, MetadataSource, SourceInfo, TimeInfo, VariableMetadata
from space_weather_analysis.metadata.overlays import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository, MongoMetadataRepository
from space_weather_analysis.util.naming import sanitize_collection_name
from space_weather_analysis.util.time_utils import ensure_aware, format_iso_duration

logger = logging.getLogger(__name__)


class GfzPipeline(MongoLoader, BasePipeline):
    """Pipeline implementation for GFZ Potsdam data ingestion.

    This pipeline handles the downloading of geomagnetic indices from GFZ
    and stores them in MongoDB. Each chunk is fetched again until it holds
    a value for every interval and none of them is preliminary.
    """

    # Time range covered by one API request
    CHUNK_DURATION = timedelta(days=30)

    # Interval length per index, used to tell whether a stored chunk is complete
    INDEX_CADENCES: Dict[str, timedelta] = {"Kp": timedelta(hours=3)}

    INDEX_DESCRIPTIONS: Dict[str, str] = {"Kp": "Planetary geomagnetic Kp index, 0 to 9 in steps of one third"}

    PRELIMINARY_STATUS = "pre"

    # Values of the 'status' field and their meaning
    STATUS_MEANINGS: Dict[str, str] = {"def": "definitive", PRELIMINARY_STATUS: "preliminary"}

    TIME_FIELD = "timestamp"

    def __init__(self, db: Database, base_data_path: pathlib.Path):
        """Initializes the GFZ pipeline.

        Args:
            db: The database connection instance.
            base_data_path: Root directory for storing downloaded files.
        """
        super().__init__(db)
        self.base_data_path = base_data_path
        self.client = GfzClient()
        self.metadata_repository: MetadataRepository = MongoMetadataRepository(db)
        self.coverage_repository: CoverageRepository = MongoCoverageRepository(db)

    def run(
        self,
        datasets: List[str],
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> None:
        """Executes the download process for the requested GFZ datasets.

        Args:
            datasets: List of GFZ index names.
            start_time: Start of the data range (inclusive); a naive value is read as UTC.
            end_time: End of the data range (exclusive); a naive value is read as UTC.
            progress_callback: Optional callback to report progress.
        """
        if not start_time or not end_time:
            logger.error("Start and end time are required for GFZ pipeline.")
            return

        start_time = ensure_aware(start_time)
        end_time = ensure_aware(end_time)
        total_datasets = len(datasets)

        for i, dataset_id in enumerate(datasets):
            logger.info(f"Processing GFZ dataset: {dataset_id}")

            # Use chunks to provide granular progress
            time_chunks = generate_time_chunks(start_time, end_time, self.CHUNK_DURATION)
            total_chunks = len(time_chunks)

            collection_name = sanitize_collection_name(dataset_id)
            self._ensure_timeseries_collection(collection_name, time_field=self.TIME_FIELD, meta_field="metadata")

            # Response of the last chunk with data, the source of the dataset metadata
            latest_response: Optional[pd.DataFrame] = None

            for j, (chunk_start, chunk_stop) in enumerate(time_chunks):
                self._report_chunk_progress(
                    progress_callback, i, total_datasets, j, total_chunks, f"Dataset {dataset_id}: Processing chunk {j + 1}/{total_chunks}"
                )

                # A failing chunk is logged and the remaining chunks still run
                try:
                    chunk_response = self._process_chunk(dataset_id, collection_name, chunk_start, chunk_stop)
                except Exception as e:
                    logger.error(f"Failed to process {dataset_id} chunk {chunk_start}-{chunk_stop}: {e}", exc_info=True)
                    continue

                if chunk_response is not None:
                    latest_response = chunk_response

            if latest_response is not None:
                self._save_metadata(dataset_id, collection_name, latest_response)
                self._refresh_coverage(self.coverage_repository, dataset_id, collection_name)

    def _process_chunk(self, dataset_id: str, collection_name: str, chunk_start: datetime, chunk_stop: datetime) -> Optional[pd.DataFrame]:
        """Fetches one chunk and replaces its stored documents, unless the stored chunk is complete and final.

        Stored documents stay untouched if the fetch delivers no rows.

        Args:
            dataset_id: The GFZ index name.
            collection_name: The collection holding the index.
            chunk_start: Start of the chunk (inclusive).
            chunk_stop: End of the chunk (exclusive).

        Returns:
            The fetched response, or None if the chunk was skipped or delivered no rows inside the chunk.
        """
        if self._is_chunk_complete_and_final(dataset_id, collection_name, chunk_start, chunk_stop):
            logger.info(f"Skipping {dataset_id} chunk {chunk_start}-{chunk_stop}: stored values are complete and definitive.")
            return None

        df = self.client.get_data(dataset_id, chunk_start, chunk_stop)

        if df.empty:
            logger.info(f"No data returned for {dataset_id} in chunk {chunk_start}-{chunk_stop}; stored documents are kept.")
            return None

        # The API includes the value at chunk_stop, which belongs to the next chunk
        chunk_rows = df[(df.index >= chunk_start) & (df.index < chunk_stop)]
        if chunk_rows.empty:
            logger.info(f"No data inside chunk {chunk_start}-{chunk_stop} for {dataset_id}; stored documents are kept.")
            return None

        documents = dataframe_to_documents(chunk_rows, f"GFZ_POTSDAM/{dataset_id}")
        self._replace_time_range(collection_name, self.TIME_FIELD, chunk_start, chunk_stop, documents)
        return df

    def _is_chunk_complete_and_final(self, dataset_id: str, collection_name: str, chunk_start: datetime, chunk_stop: datetime) -> bool:
        """Tells whether the stored chunk holds one document per interval and no preliminary value.

        Args:
            dataset_id: The GFZ index name.
            collection_name: The collection holding the index.
            chunk_start: Start of the chunk (inclusive).
            chunk_stop: End of the chunk (exclusive).

        Returns:
            True if the chunk needs no request; always False for an index without a known cadence.
        """
        cadence = self.INDEX_CADENCES.get(dataset_id)
        if cadence is None:
            return False

        chunk_filter: Dict[str, Any] = {self.TIME_FIELD: {"$gte": chunk_start, "$lt": chunk_stop}}
        collection = self.db[collection_name]

        stored_count = collection.count_documents(chunk_filter)
        if stored_count != count_grid_times(chunk_start, chunk_stop, cadence):
            return False

        preliminary_count = collection.count_documents({**chunk_filter, "data.status": self.PRELIMINARY_STATUS})
        return preliminary_count == 0

    def _save_metadata(self, dataset_id: str, collection_name: str, response: pd.DataFrame) -> None:
        """Saves the metadata record of an index, with license and provider from the response meta.

        Variable descriptions, status meanings, cadence and time stamp location are values of this
        class; license and provider come from the meta object GFZ sends with every response.
        A failing save is logged, as the stored data documents stay valid without metadata.

        Args:
            dataset_id: The GFZ index name.
            collection_name: The collection holding the index.
            response: A fetched response of the index, carrying the response meta in attrs["meta"].
        """
        meta: Dict[str, Any] = response.attrs.get("meta", {})
        retrieved_at = datetime.now(timezone.utc)
        description = self.INDEX_DESCRIPTIONS.get(dataset_id, f"GFZ geomagnetic index {dataset_id}")
        cadence = self.INDEX_CADENCES.get(dataset_id)

        variables = [VariableMetadata.from_single_source("curated", name=dataset_id, source_name=dataset_id, description=description, dtype="double")]
        if "status" in response.columns:
            variables.append(
                VariableMetadata.from_single_source(
                    "curated",
                    name="status",
                    source_name="status",
                    description="Status of the index value",
                    dtype="string",
                    flag_meanings=dict(self.STATUS_MEANINGS),
                )
            )

        record = DatasetMetadata(
            dataset_id=dataset_id,
            collection=collection_name,
            source=SourceInfo(type=SourceType.GFZ_POTSDAM, provider=meta.get("source"), server_url=self.client.BASE_URL),
            # GFZ timestamps mark the start of each interval
            time=TimeInfo(cadence=format_iso_duration(cadence) if cadence else None, timestamp_location="begin"),
            citation=CitationInfo(license=meta.get("license")),
            variables=variables,
            provenance=[
                MetadataSource(kind="api", url=self.client.BASE_URL, retrieved_at=retrieved_at),
                MetadataSource(kind="curated", retrieved_at=retrieved_at),
            ],
            updated_at=retrieved_at,
        )
        try:
            save_with_overlays(self.metadata_repository, [record])
        except Exception as e:
            logger.error(f"Failed to save metadata of {dataset_id}: {e}", exc_info=True)


def count_grid_times(start: datetime, stop: datetime, cadence: timedelta) -> int:
    """Counts the interval start times in [start, stop) on a grid anchored at 00:00 UTC.

    Args:
        start: Start of the range (inclusive), timezone-aware.
        stop: End of the range (exclusive), timezone-aware.
        cadence: Grid spacing; it must divide one day.

    Returns:
        The number of grid times inside the range.
    """
    day_start = start.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    # The first grid time at or after start
    first_grid_time = day_start + math.ceil((start - day_start) / cadence) * cadence
    if first_grid_time >= stop:
        return 0
    # Grid times first_grid_time, first_grid_time + cadence, ... before stop
    return math.ceil((stop - first_grid_time) / cadence)
