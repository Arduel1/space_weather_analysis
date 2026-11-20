from datetime import UTC, timedelta

import pandas as pd
import pytest
from pymongo.database import Database

from space_weather_analysis.dashboard.reading import RepositoryReader
from space_weather_analysis.jobs.repository import MongoJobRepository
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository


@pytest.mark.integration
def test_recent_of_a_dataset_without_documents_is_empty_with_a_utc_time_index(db: Database) -> None:
    """A dataset not loaded yet reads as an empty frame that panels can filter and subtract by time like a filled one."""
    reader = RepositoryReader(MongoTimeSeriesRepository(db), MongoMetadataRepository(db), MongoJobRepository(db))

    frame = reader.recent("synthetic_flux", timedelta(hours=1), ("flux",))

    assert isinstance(frame.index, pd.DatetimeIndex)
    assert (frame.empty, list(frame.columns), frame.index.tz, len(frame[frame.index >= pd.Timestamp(2024, 1, 1, tz=UTC)])) == (True, ["flux"], UTC, 0)
