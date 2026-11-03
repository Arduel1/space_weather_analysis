from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from pipeline_service.pipelines.report import IngestionReport

# Receives the progress of a run, from 0.0 to 1.0, and a status message.
ProgressCallback = Callable[[float, str], None]


class Pipeline(Protocol):
    """The interface by which the job runner runs every data pipeline."""

    def run(
        self,
        datasets: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        progress_callback: ProgressCallback | None = None,
    ) -> IngestionReport:
        """Ingests the datasets for a time range.

        Args:
            datasets: The IDs of the datasets to ingest.
            start_time: The start of the time range (inclusive).
            end_time: The end of the time range (exclusive).
            progress_callback: Receives the progress of the run; None to report nothing.

        Returns:
            The report with the number of tried chunks and the chunks that failed.

        Raises:
            ValueError: If the request is invalid, so no chunk can be tried.
        """
