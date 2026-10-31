import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import ClassVar

import pandas as pd

from pipeline_service.pipelines.processors.base import BaseETLProcessor
from pipeline_service.util.documents import DocumentDict, frame_to_documents
from space_weather_analysis.util.error_handling import DataProcessingError

logger = logging.getLogger(__name__)


class CmeETL(BaseETLProcessor):
    """Loads the SOHO/LASCO catalog of coronal mass ejections (CME) from its text file."""

    COLLECTION_NAME = "cme_catalog"
    # Identifies the source and version of the catalog in the metadata field of every document.
    SOURCE_IDENTIFIER = "SOHO/LASCO_CDAW_V2.0"

    _NUMERIC_COLUMNS = (
        "central_pa",
        "width",
        "linear_speed",
        "speed_initial",
        "speed_final",
        "speed_at_20r",
        "accel",
        "mass",
        "kinetic_energy",
        "mpa",
    )
    # Column order of the catalog file; the numeric columns sit between time and remarks.
    _COLUMN_NAMES = ("date", "time", *_NUMERIC_COLUMNS, "remarks")
    # The catalog file starts with 4 header lines.
    _HEADER_LINE_COUNT = 4
    # Dash runs that the catalog writes in place of an unknown value.
    _PLACEHOLDERS = frozenset({"----", "------", "-------"})
    # Columns in which a trailing '*' marks an uncertain value, mapped to the flag field storing that marker.
    _MARKER_FLAGS: ClassVar[Mapping[str, str]] = MappingProxyType(
        {
            "accel": "accel_unreliable",
            "mass": "mass_uncertain",
            "kinetic_energy": "kinetic_energy_uncertain",
        }
    )

    def _extract(self) -> pd.DataFrame:
        """Splits the rows of the space-delimited catalog file into its columns, all as text.

        Returns:
            The raw rows of the file.

        Raises:
            FileNotFoundError: If the file does not exist.
            DataProcessingError: If the file cannot be read.
        """
        if not self.file_path.exists():
            raise FileNotFoundError(f"CME catalog file not found at {self.file_path}")
        try:
            lines = self.file_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            raise DataProcessingError(f"CME catalog file {self.file_path} cannot be read: {error}") from error

        parsed_rows: list[dict[str, str]] = []
        for line in lines[self._HEADER_LINE_COUNT :]:
            # The remarks are the rest of the line after the 12th column and keep their inner spacing.
            parts = line.rstrip().split(maxsplit=12)
            if not parts:
                continue
            # Rows without remarks have 12 parts and get no remarks entry.
            parsed_rows.append(dict(zip(self._COLUMN_NAMES, parts, strict=False)))
        logger.info("Extracted %d rows from %s.", len(parsed_rows), self.file_path)
        return pd.DataFrame(parsed_rows, columns=list(self._COLUMN_NAMES))

    def _transform(self, raw_data: pd.DataFrame) -> list[DocumentDict]:
        """Builds one document per event with a valid time, with numbers, halo flag and uncertainty flags.

        Args:
            raw_data: The raw rows of the file, left unchanged.

        Returns:
            The time series documents.
        """
        raw_data = raw_data.copy()
        for column, flag in self._MARKER_FLAGS.items():
            is_marked = raw_data[column].str.endswith("*", na=False)
            # The flag is stored as true for marked values only; unmarked events get no flag field.
            raw_data[flag] = is_marked.map({True: True, False: None})
            raw_data[column] = raw_data[column].str.removesuffix("*")

        raw_data["timestamp"] = pd.to_datetime(
            raw_data["date"].astype(str) + " " + raw_data["time"].astype(str),
            format="%Y/%m/%d %H:%M:%S",
            errors="coerce",
        ).dt.tz_localize("UTC")
        has_valid_timestamp = raw_data["timestamp"].notna()
        if not has_valid_timestamp.all():
            logger.warning("Skipped %d rows without a valid timestamp.", (~has_valid_timestamp).sum())
        raw_data = raw_data[has_valid_timestamp].copy()

        raw_data["is_halo"] = raw_data["central_pa"].str.contains("Halo", na=False)
        # A halo CME has no central position angle; is_halo carries that information.
        raw_data["central_pa"] = raw_data["central_pa"].mask(raw_data["is_halo"])

        for column in self._NUMERIC_COLUMNS:
            is_placeholder = raw_data[column].isin(self._PLACEHOLDERS)
            numeric = pd.to_numeric(raw_data[column].mask(is_placeholder), errors="coerce")
            # Values that are neither numbers nor placeholders are invalid and omitted like missing ones.
            invalid_count = (numeric.isna() & raw_data[column].notna() & ~is_placeholder).sum()
            if invalid_count:
                logger.warning("Omitted %d invalid values in field %s.", invalid_count, column)
            raw_data[column] = numeric

        data_columns = [
            *self._NUMERIC_COLUMNS,
            "is_halo",
            *self._MARKER_FLAGS.values(),
            "remarks",
        ]

        return frame_to_documents(raw_data["timestamp"].to_list(), raw_data[data_columns], self.SOURCE_IDENTIFIER)
