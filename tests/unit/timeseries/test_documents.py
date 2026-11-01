from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.timeseries.documents import frame_to_documents, source_identifier

SOURCE = "LOCAL/SYNTHETIC"
SERVER_URL = "https://synthetic.example"


def test_frame_to_documents_omits_missing_values_and_stores_aware_utc_times() -> None:
    """Missing values of any kind are left out per row, and a naive index is stored as aware UTC."""
    frame = pd.DataFrame(
        {"value": [1.5, np.nan], "flag": pd.array([pd.NA, 2], dtype="Int64"), "label": ["a", None]},
        index=pd.DatetimeIndex(["2024-01-01T00:00:00", "2024-01-01T01:00:00"]),
    )

    documents = frame_to_documents(frame, SOURCE, SERVER_URL)

    assert documents == [
        {
            "timestamp": datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
            "metadata": {"source": SOURCE, "server": SERVER_URL},
            "data": {"value": 1.5, "label": "a"},
        },
        {"timestamp": datetime(2024, 1, 1, 1, 0, tzinfo=UTC), "metadata": {"source": SOURCE, "server": SERVER_URL}, "data": {"flag": 2}},
    ]


def test_frame_to_documents_without_server_converts_aware_index_to_utc() -> None:
    """Without a server URL the metadata holds only the source; an aware index is converted to UTC."""
    frame = pd.DataFrame({"value": [3.0]}, index=pd.DatetimeIndex(["2024-01-01T02:00:00+02:00"]))

    documents = frame_to_documents(frame, source_identifier(SourceType.LOCAL, "SYNTHETIC"))

    assert documents == [{"timestamp": datetime(2024, 1, 1, 0, 0, tzinfo=UTC), "metadata": {"source": SOURCE}, "data": {"value": 3.0}}]


def test_frame_to_documents_rejects_frame_without_datetime_index() -> None:
    """A frame without a DatetimeIndex has no time stamps to store."""
    with pytest.raises(ValueError, match="DatetimeIndex"):
        frame_to_documents(pd.DataFrame({"value": [1.0]}), SOURCE)
