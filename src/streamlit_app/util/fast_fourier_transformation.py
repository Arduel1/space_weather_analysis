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
        series (pd.Series): The time series data. Index must be DatetimeIndex.
        sample_rate_days (int): The interval between data points in days (default: 1).
        min_period_years (float): Minimum cycle length to keep in results (filter noise).
        max_period_years (float): Maximum cycle length to keep (filter trends).

    Returns:
        pd.DataFrame: A DataFrame sorted by Power (strength of the cycle) descending.
                      Columns: ['Frequency', 'Period_Days', 'Period_Years', 'Power']
    """
    if series.empty:
        return pd.DataFrame()

    try:
        # 1. Preprocessing: Ensure data is numeric and fill gaps
        # FFT requires equidistant data points without NaNs.
        # We resample to ensure a fixed daily frequency and interpolate missing values.
        clean_series = series.resample(f"{sample_rate_days}D").mean().interpolate(method="linear")

        # Drop any remaining NaNs (e.g., at the very start)
        clean_series = clean_series.dropna()

        if len(clean_series) < 2:
            return pd.DataFrame()

        # 2. Detrending (Optional but recommended for pure cycle detection)
        # We subtract the mean to center the oscillation around 0.
        # For more complex trends, one might subtract a rolling mean, but this suffices.
        values = clean_series.values - clean_series.mean()
        n_samples = len(values)

        # 3. Perform Fast Fourier Transform (FFT) using NumPy
        # rfft computes the one-dimensional discrete Fourier Transform for real input.
        fft_values = np.fft.rfft(values)

        # Calculate the Power Spectrum (Amplitude squared)
        # We normalize by the number of samples to keep values interpretable.
        power_spectrum = np.abs(fft_values) ** 2 / n_samples

        # 4. Calculate Frequencies associated with the FFT result
        # rfftfreq returns the frequency bins (cycles per sampling interval)
        freqs = np.fft.rfftfreq(n_samples, d=sample_rate_days)

        # 5. Build Result DataFrame
        results = pd.DataFrame({"Frequency": freqs, "Power": power_spectrum})

        # Filter out the 0-frequency component (the static mean/offset)
        results = results[results["Frequency"] > 0].copy()

        # 6. Convert Frequency to Human-Readable Periods
        # Period = 1 / Frequency
        results["Period_Days"] = 1 / results["Frequency"]
        results["Period_Years"] = results["Period_Days"] / 365.25

        # 7. Filter relevant ranges (e.g., ignore cycles < 6 months or > 20 years)
        mask = (results["Period_Years"] >= min_period_years) & (results["Period_Years"] <= max_period_years)
        filtered_results = results[mask].copy()

        # Sort by Power to show dominant cycles first
        return filtered_results.sort_values(by="Power", ascending=False)

    except Exception as e:
        logger.error(f"Error calculating periodicity: {e}", exc_info=True)
        return pd.DataFrame()
