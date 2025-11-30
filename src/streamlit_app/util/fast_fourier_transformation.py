import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def calculate_periodicity(
    series: pd.Series, sample_rate_days: int = 1, min_period_years: float = 0.5, max_period_years: float = 20.0
) -> pd.DataFrame:
    """
    Performs a Fourier analysis (FFT) to identify dominant cycles in a time series.

    This function handles the necessary preprocessing (interpolation, detrending)
    and returns a user-friendly DataFrame correlating periods (in years) with their
    spectral power (intensity).

    Args:
        series: The time series data. Index must be DatetimeIndex.
        sample_rate_days: The interval between data points in days (default: 1).
        min_period_years: Minimum cycle length to keep in results (filter noise).
        max_period_years: Maximum cycle length to keep (filter trends).

    Returns:
        A DataFrame sorted by Power (strength of the cycle) descending.
        Columns: ['Frequency', 'Period_Days', 'Period_Years', 'Power']
    """
    if series.empty:
        return pd.DataFrame()

    try:
        # Preprocessing: FFT requires equidistant data without NaNs
        clean_series = series.resample(f"{sample_rate_days}D").mean().interpolate(method="linear")
        clean_series = clean_series.dropna()

        if len(clean_series) < 2:
            return pd.DataFrame()

        # Detrending: Center the oscillation around 0
        values = clean_series.values - clean_series.mean() # type: ignore
        n_samples = len(values)

        # FFT Calculation using NumPy
        fft_values = np.fft.rfft(values)

        # Power Spectrum: Normalized amplitude squared
        power_spectrum = np.abs(fft_values) ** 2 / n_samples

        # Frequency Bins: Cycles per sampling interval
        freqs = np.fft.rfftfreq(n_samples, d=sample_rate_days)

        results = pd.DataFrame({"Frequency": freqs, "Power": power_spectrum})

        # Remove static offset (0-frequency)
        results = results[results["Frequency"] > 0].copy()

        # Convert Frequency to Human-Readable Periods
        results["Period_Days"] = 1 / results["Frequency"]
        results["Period_Years"] = results["Period_Days"] / 365.25

        # Filter relevant cycle ranges
        mask = (results["Period_Years"] >= min_period_years) & (results["Period_Years"] <= max_period_years)
        filtered_results = results[mask].copy()

        return filtered_results.sort_values(by="Power", ascending=False)

    except Exception as e:
        logger.error(f"Error calculating periodicity: {e}", exc_info=True)
        return pd.DataFrame()
