from datetime import datetime
from typing import Any, NamedTuple

import numpy as np
import pandas as pd
import requests

from space_weather_analysis.clients.download import download_json
from space_weather_analysis.clients.swpc import SwpcClient
from space_weather_analysis.geospace.aurora import wrap_longitude
from space_weather_analysis.util.error_handling import DataProcessingError

# The newest map of the OVATION aurora model (Oval Variation, Assessment, Tracking, Intensity, and Online Nowcasting)
# of SWPC: the probability of aurora in percent on a grid of one degree in geographic longitude (0 to 359, east) and
# latitude (-90 to 90), computed from the solar wind measured at L1 for a time about half an hour after the
# measurement; L1 is the Lagrange point 1.5 million km sunwards of Earth
OVATION_URL = f"{SwpcClient.DEFAULT_BASE_URL}json/ovation_aurora_latest.json"
OVATION_PAGE_URL = "https://www.spaceweather.gov/products/aurora-30-minute-forecast"
# Connect and read timeout in seconds
OVATION_TIMEOUT = (10.0, 60.0)


class AuroraGrid(NamedTuple):
    """One map of the OVATION model.

    Attributes:
        observation_time: The UTC time of the solar wind measurement the map is computed from.
        forecast_time: The UTC time the map is valid for.
        longitude: The geographic longitude of each cell in degrees, east, from -180 to 179.
        latitude: The geographic latitude of each cell in degrees.
        probability: The probability of aurora of each cell in percent.
    """

    observation_time: datetime
    forecast_time: datetime
    longitude: np.ndarray
    latitude: np.ndarray
    probability: np.ndarray


def parse_grid(content: Any) -> AuroraGrid:
    """Reads an OVATION map from its JSON document.

    Args:
        content: The parsed JSON document with `Observation Time`, `Forecast Time` and `coordinates` as rows of
            longitude, latitude and probability.

    Returns:
        The map with longitudes turned to -180 to 179 degrees.

    Raises:
        DataProcessingError: If the document lacks a time or the rows.
    """
    if not isinstance(content, dict) or not isinstance(content.get("coordinates"), list):
        raise DataProcessingError("The OVATION document holds no coordinates.")
    observation = pd.to_datetime(str(content.get("Observation Time") or ""), utc=True, errors="coerce")
    forecast = pd.to_datetime(str(content.get("Forecast Time") or ""), utc=True, errors="coerce")
    if pd.isna(observation) or pd.isna(forecast):
        raise DataProcessingError("The OVATION document lacks its observation or forecast time.")
    try:
        rows = np.asarray(content["coordinates"], dtype=float)
    except ValueError as error:
        raise DataProcessingError(f"The OVATION rows are not numbers: {error}") from error
    if rows.ndim != 2 or rows.shape[1] != 3:
        raise DataProcessingError("The OVATION rows are not triples of longitude, latitude and probability.")
    # The file counts longitude from 0 to 359 east; the stored maps from -180 to 179
    longitude = wrap_longitude(rows[:, 0])
    return AuroraGrid(observation.to_pydatetime(), forecast.to_pydatetime(), longitude, rows[:, 1], rows[:, 2])


def fetch_grid(session: requests.Session) -> AuroraGrid:
    """Fetches the newest OVATION map.

    Args:
        session: The HTTP session for the request.

    Returns:
        The map.

    Raises:
        SourceUnreachableError: If SWPC sent no answer.
        DataRetrievalError: If the download failed.
        DataProcessingError: If the document cannot be read.
    """
    return parse_grid(download_json(session, OVATION_URL, OVATION_TIMEOUT, "OVATION aurora map"))
