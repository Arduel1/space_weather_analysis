import threading
from dataclasses import dataclass

from pipeline_service.pipelines.report import IngestionReport
from space_weather_analysis.timeseries.repository import WriteCount


@dataclass
class FeedTotals:
    """What the jobs of one feed did since the last summary.

    Attributes:
        runs: The finished jobs.
        failed_runs: The jobs that ended FAILED.
        inserted: The inserted documents.
        deleted: The deleted documents.
        chunks_skipped: The chunks not requested, as their last fetch was current.
        gaps: The chunks that failed.
    """

    runs: int = 0
    failed_runs: int = 0
    inserted: int = 0
    deleted: int = 0
    chunks_skipped: int = 0
    gaps: int = 0


class FeedActivity:
    """Sums what the jobs of each feed did since the last summary.

    The job runner logs one summary per feed and interval instead of a line per job. Safe to share between its lanes.
    """

    def __init__(self) -> None:
        """Creates the sums, empty."""
        self._totals: dict[str, FeedTotals] = {}
        self._lock = threading.Lock()

    def record(self, feed: str, report: IngestionReport | None, writes: WriteCount) -> None:
        """Adds a finished job of a feed.

        Args:
            feed: The name of the feed.
            report: The report of the pipeline run; None if the job failed without one.
            writes: The documents the job wrote.
        """
        with self._lock:
            totals = self._totals.setdefault(feed, FeedTotals())
            totals.runs += 1
            totals.inserted += writes.inserted
            totals.deleted += writes.deleted
            if report is None or report.all_failed:
                totals.failed_runs += 1
            if report is not None:
                totals.chunks_skipped += report.chunks_skipped
                totals.gaps += len(report.gaps)

    def take(self) -> dict[str, FeedTotals]:
        """Returns the sums of every feed with a finished job since the last call and starts new sums.

        Returns:
            The sums by feed name, sorted by name.
        """
        with self._lock:
            totals, self._totals = self._totals, {}
        return dict(sorted(totals.items()))
