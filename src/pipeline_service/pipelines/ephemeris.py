from datetime import datetime, timedelta

import pandas as pd

from pipeline_service.pipelines.base import ProgressCallback
from pipeline_service.pipelines.chunks import refresh_coverage, require_single_dataset, resolve_time_range, run_single_dataset, save_minimal_metadata
from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.clients.ephemeris import (
    BODY_FIELD,
    HORIZONS_PAGE_URL,
    POSITION_FIELDS,
    Body,
    Locator,
    body_positions,
    configured_bodies,
    daily_times,
    locate,
)
from space_weather_analysis.coverage.repository import CoverageRepository
from space_weather_analysis.metadata.repository import MetadataRepository
from space_weather_analysis.timeseries.documents import frame_to_documents, source_identifier
from space_weather_analysis.timeseries.models import Granularity
from space_weather_analysis.timeseries.repository import TimeSeriesRepository
from space_weather_analysis.util.naming import sanitize_collection_name

# The one dataset holding the daily positions of all bodies, one document per body and day
POSITIONS_DATASET = "body_positions"
# Positions lie a day apart; hours is the coarsest granularity of a time series collection
POSITIONS_GRANULARITY = Granularity.HOURS
# Range of a run without start time
DEFAULT_LOOKBACK = timedelta(days=30)
# Span the positions reach after now, so that the arrivals WSA-Enlil expects for the coming days find their targets
POSITIONS_AHEAD = timedelta(days=10)
# Time after the last position up to which the stored positions are replaced, as the end of a range is exclusive
RANGE_END_MARGIN = timedelta(seconds=1)


class EphemerisPipeline:
    """Stores the daily positions of the planets and spacecraft of the bodies file, from the range start to some days ahead.

    Each run computes the whole range again, one Horizons request per spacecraft, and replaces the stored positions, so
    that improved navigation solutions are taken over.
    """

    def __init__(
        self,
        *,
        timeseries: TimeSeriesRepository,
        coverage_repository: CoverageRepository,
        metadata_repository: MetadataRepository,
        bodies: dict[str, Body] | None = None,
        locator: Locator = locate,
    ) -> None:
        """Initializes the ephemeris pipeline.

        Args:
            timeseries: The repository storing the position documents.
            coverage_repository: The repository storing which time ranges are stored.
            metadata_repository: The repository storing the metadata records.
            bodies: The bodies by name; None reads the bodies file of the settings.
            locator: Computes the positions of one body.

        Raises:
            OSError: If the bodies file of the settings cannot be read.
            tomllib.TOMLDecodeError: If the bodies file is no valid TOML.
            pydantic.ValidationError: If a body is invalid.
        """
        self.timeseries = timeseries
        self.coverage_repository = coverage_repository
        self.metadata_repository = metadata_repository
        self.bodies = bodies if bodies is not None else configured_bodies()
        self.locator = locator

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Stores the positions from the start to POSITIONS_AHEAD after the end.

        Args:
            datasets: The dataset IDs; only POSITIONS_DATASET is known.
            start_time: The start of the time range (inclusive); None starts DEFAULT_LOOKBACK before the end.
            end_time: The end of the time range; None ends now.
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with one chunk for the range, a gap if no position could be computed or stored.

        Raises:
            ValueError: If a dataset other than POSITIONS_DATASET is given.
        """
        require_single_dataset(datasets, POSITIONS_DATASET, "ephemeris")
        start, stop = resolve_time_range(start_time, end_time, DEFAULT_LOOKBACK)
        # Midnights UTC from the day of the start to POSITIONS_AHEAD after the end
        times = daily_times(start, stop + POSITIONS_AHEAD)
        return run_single_dataset(
            POSITIONS_DATASET,
            (start, stop),
            lambda: self._store_positions(times),
            what="positions of the bodies",
            progress_callback=progress_callback,
        )

    def _store_positions(self, times: list[datetime]) -> str | None:
        """Computes the positions of every body at the times and replaces the stored positions of these days.

        Returns:
            The reason of a gap if no position could be computed; None once positions are stored.

        Raises:
            pymongo.errors.PyMongoError: If the documents cannot be read or stored.
        """
        positions, failed = body_positions(self.bodies, times, self.locator)
        if positions.empty:
            return f"No position computed for {', '.join(failed)}"
        collection = sanitize_collection_name(POSITIONS_DATASET)
        self.timeseries.ensure_collection(collection, POSITIONS_GRANULARITY)
        range_stop = times[-1] + RANGE_END_MARGIN
        # A body whose positions failed in this run keeps its stored ones until the next run
        stored = self.timeseries.read_range(collection, times[0], range_stop, list(POSITION_FIELDS))
        if failed and not stored.empty:
            positions = pd.concat([positions, stored[stored[BODY_FIELD].isin(failed)]]).sort_index(kind="stable")
        documents = frame_to_documents(positions, source_identifier(SourceType.EPHEMERIS, POSITIONS_DATASET))
        self.timeseries.replace_range(collection, times[0], range_stop, documents)
        refresh_coverage(self.coverage_repository, POSITIONS_DATASET, collection)
        self._save_metadata(collection)
        return None

    def _save_metadata(self, collection: str) -> None:
        """Saves the metadata record of the positions; a failing save is logged, as the documents stay valid without it."""
        save_minimal_metadata(self.metadata_repository, POSITIONS_DATASET, collection, SourceType.EPHEMERIS, POSITION_FIELDS, HORIZONS_PAGE_URL)
