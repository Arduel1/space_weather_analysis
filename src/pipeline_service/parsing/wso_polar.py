import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from space_weather_analysis.util.error_handling import DataProcessingError

logger = logging.getLogger(__name__)

# Identifies the polar field observations of the Wilcox Solar Observatory (WSO) at Stanford.
WSO_POLAR_PROVIDER_ID = "WSO_POLAR"

# An observation line of the WSO polar field page holds the time, the north, south and average field, the words
# `20nhz filt:` and the same three after a low pass filter of 20 nHz, such as
# `1976:05:31_21h:07m:13s    89N -126S  108Avg   20nhz filt:  111Nf  -92Sf  101Avgf`; the strptime format of its time:
_TIME_FORMAT = "%Y:%m:%d_%Hh:%Mm:%Ss"
# The words between the measured and the filtered values
_FILTER_WORDS = ["20nhz", "filt:"]
# Column names of the six field values of an observation line and the unit letters each value ends with on the line
_FIELDS = ("north", "south", "average", "north_filtered", "south_filtered", "average_filtered")
_VALUE_SUFFIXES = ("N", "S", "Avg", "Nf", "Sf", "Avgf")


def _observation(line: str) -> tuple[datetime, list[int]] | None:
    """Reads the time and the six field values of an observation line; None for any other line."""
    words = line.split()
    if len(words) < 9 or words[4:6] != _FILTER_WORDS:
        return None
    try:
        time = datetime.strptime(words[0], _TIME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
    values = []
    # The value words around the filter words, each a whole number followed by its unit letters
    for word, suffix in zip(words[1:4] + words[6:9], _VALUE_SUFFIXES, strict=True):
        number = word.removesuffix(suffix)
        if number == word or not number.removeprefix("-").isdecimal():
            return None
        values.append(int(number))
    return time, values


def parse_wso_polar_fields(path: Path) -> pd.DataFrame:
    """Reads the polar field observations of the WSO polar field page.

    WSO averages the daily polar field measurements of a centred 30-day window every 10 days; the filtered values remove
    the yearly projection effect. Lines of the page that are no observation are left out.

    Args:
        path: The saved HTML page of the WSO polar field observations.

    Returns:
        One row per observation with a timezone-aware UTC DatetimeIndex and the columns north, south, average,
        north_filtered, south_filtered and average_filtered in the units of WSO.

    Raises:
        FileNotFoundError: If the file does not exist.
        DataProcessingError: If the page holds no observation.
    """
    if not path.exists():
        raise FileNotFoundError(f"WSO polar field page not found at {path}")
    observations = [observation for line in path.read_text(encoding="latin-1").splitlines() if (observation := _observation(line)) is not None]
    if not observations:
        raise DataProcessingError(f"{path} holds no WSO polar field observation.")
    times = pd.DatetimeIndex([time for time, _ in observations])
    values = pd.DataFrame([row for _, row in observations], columns=list(_FIELDS), index=times).astype(int)
    logger.info("Extracted %d polar field observations from %s.", len(values), path)
    return values[~values.index.duplicated()].sort_index()
