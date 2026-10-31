import logging

import pandas as pd

from pipeline_service.pipelines.processors.base import BaseETLProcessor
from pipeline_service.util.documents import DocumentDict, frame_to_documents
from space_weather_analysis.util.error_handling import DataProcessingError

logger = logging.getLogger(__name__)


class SilsoETL(BaseETLProcessor):
    """Loads the daily total sunspot numbers of SILSO (Sunspot Index and Long-term Solar Observations) from their CSV file."""

    COLLECTION_NAME = "silso_sunspot_daily"
    # Identifies the source and version of the data in the metadata field of every document.
    SOURCE_IDENTIFIER = "SILSO_V2.0"

    _COLUMN_NAMES = (
        "year",
        "month",
        "day",
        "decimal_year",
        "sunspot_number",
        "std_dev",
        "num_observations",
        "indicator",
    )
    # Fields that hold placeholders instead of measurements on days without a sunspot number (-1).
    _FIELDS_UNSET_ON_MISSING_DAY = ("sunspot_number", "std_dev", "num_observations")

    def _extract(self) -> pd.DataFrame:
        """Reads the semicolon-separated SILSO file with every field as text.

        Returns:
            The raw rows of the file.

        Raises:
            FileNotFoundError: If the file does not exist.
            DataProcessingError: If the file cannot be read.
        """
        if not self.file_path.exists():
            raise FileNotFoundError(f"SILSO data file not found at {self.file_path}")
        try:
            raw_data = pd.read_csv(
                self.file_path,
                sep=";",
                names=list(self._COLUMN_NAMES),
                # Text columns let the transform step convert and validate each field on its own.
                dtype=str,
                skipinitialspace=True,
            )
        except (OSError, UnicodeDecodeError, pd.errors.ParserError) as error:
            raise DataProcessingError(f"SILSO data file {self.file_path} cannot be read: {error}") from error
        logger.info("Extracted %d rows from %s.", len(raw_data), self.file_path)
        return raw_data

    def _transform(self, raw_data: pd.DataFrame) -> list[DocumentDict]:
        """Builds one document per row with a valid date, omitting the placeholders of days without a sunspot number.

        Args:
            raw_data: The raw rows of the file.

        Returns:
            The time series documents.
        """
        # An unparsable value becomes NA in its own column only, so the rest of the row stays usable.
        numeric = raw_data.apply(pd.to_numeric, errors="coerce")

        timestamps_all = pd.to_datetime(numeric[["year", "month", "day"]], errors="coerce").dt.tz_localize("UTC")
        has_valid_date = timestamps_all.notna()
        if not has_valid_date.all():
            logger.warning("Skipped %d rows without a valid date.", (~has_valid_date).sum())
        numeric = numeric[has_valid_date]

        is_missing = numeric["sunspot_number"] == -1
        data = pd.DataFrame(
            {
                "sunspot_number": self._to_count(numeric["sunspot_number"]).mask(is_missing),
                "std_dev": numeric["std_dev"].mask(is_missing),
                "num_observations": self._to_count(numeric["num_observations"]).mask(is_missing),
                # SILSO marks provisional values with indicator 0 and definitive values with 1.
                "is_provisional": numeric["indicator"].map({0: True, 1: False}),
                "decimal_year": numeric["decimal_year"],
            }
        )

        # A field counts as invalid if the file has a value for it that is neither valid nor a documented placeholder.
        raw_rows = raw_data.loc[has_valid_date]
        # Renamed so the file column indicator lines up with the output field derived from it.
        raw_fields = raw_rows.rename(columns={"indicator": "is_provisional"})[data.columns]
        given = raw_fields.notna()
        invalid = data.isna() & given
        invalid.loc[is_missing, list(self._FIELDS_UNSET_ON_MISSING_DAY)] = False
        for column, count in invalid.sum().items():
            if count:
                logger.warning("Omitted %d invalid values in field %s.", count, column)

        return frame_to_documents(timestamps_all[has_valid_date].to_list(), data, self.SOURCE_IDENTIFIER)

    @staticmethod
    def _to_count(values: pd.Series) -> pd.Series:
        """Converts whole numbers to a nullable integer column.

        Args:
            values: Numeric values, NA where the file held no parsable number.

        Returns:
            The values as Int64; fractional values become NA.
        """
        return values.where(values % 1 == 0).astype("Int64")
