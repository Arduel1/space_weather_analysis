from datetime import timedelta

import pandas as pd


def find_minima(values: pd.Series, separation: timedelta) -> pd.DatetimeIndex:
    """Finds the times of the minima of a smooth series that lie at least a separation apart.

    Args:
        values: The smoothed values with a UTC DatetimeIndex.
        separation: The shortest time between two minima.

    Returns:
        The times of the minima, oldest first.
    """
    present = values.dropna().sort_index()
    if present.empty:
        return pd.DatetimeIndex([], tz="UTC")
    # A value is a minimum when no value within half the separation before or after it is lower
    half = pd.Timedelta(separation) / 2
    lowest_around = present.rolling(pd.Timedelta(separation), center=True, min_periods=1).min()
    # Values closer than half the separation to either end have an incomplete neighbourhood and are never minima
    complete = (present.index - present.index[0] >= half) & (present.index[-1] - present.index >= half)
    candidates = present.index[(present <= lowest_around) & complete]
    # Equal lowest values in a row are one minimum, kept at its first time
    minima: list[pd.Timestamp] = []
    for time in candidates:
        if not minima or time - minima[-1] >= half:
            minima.append(time)
    return pd.DatetimeIndex(minima)
