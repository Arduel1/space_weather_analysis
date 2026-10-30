from dataclasses import dataclass, field

from space_weather_analysis.jobs.models import ChunkGap


@dataclass
class IngestionReport:
    """What one pipeline run tried to ingest and which time ranges it could not.

    A failed chunk only leaves a gap in the coverage, so the run goes on with the next chunk.

    Attributes:
        chunks_total: The chunks the run tried to ingest, without the chunks skipped as already stored.
        gaps: The chunks that failed.
    """

    chunks_total: int = 0
    gaps: list[ChunkGap] = field(default_factory=list)

    @property
    def all_failed(self) -> bool:
        """Whether the run tried at least one chunk and every one failed."""
        return self.chunks_total > 0 and len(self.gaps) == self.chunks_total

    def merge(self, other: "IngestionReport") -> None:
        """Adds the chunks and gaps of another report to this one.

        Args:
            other: The report of a further run.
        """
        self.chunks_total += other.chunks_total
        self.gaps.extend(other.gaps)
