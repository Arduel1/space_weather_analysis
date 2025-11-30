import logging

import pandas as pd

logger = logging.getLogger(__name__)


def aggregate_categorical_time_distribution(df: pd.DataFrame, categorical_col: str, freq: str = "YE") -> pd.DataFrame:
    """
    Aggregates a categorical column over time to show the distribution of classes per period.
    Useful for visualizing trends, e.g., "How did the ratio of Halo CMEs change per year?".

    Args:
        df (pd.DataFrame): DataFrame with a DatetimeIndex.
        categorical_col (str): The column containing categories (boolean or string).
        freq (str): Pandas frequency string (default 'YE' for Year End).

    Returns:
        pd.DataFrame: A DataFrame where the index is time, columns are categories,
                      and values are counts.
    """
    if df.empty or categorical_col not in df.columns:
        return pd.DataFrame()

    try:
        # Group by Time Period AND Category, then count sizes
        # unstack moves the categories from rows to columns
        counts = df.groupby([pd.Grouper(freq=freq), categorical_col]).size().unstack(fill_value=0)

        # Determine the total count per period to calculate percentages if needed later
        counts["Total"] = counts.sum(axis=1)

        return counts
    except Exception as e:
        logger.error(f"Error aggregating categorical data: {e}", exc_info=True)
        return pd.DataFrame()
