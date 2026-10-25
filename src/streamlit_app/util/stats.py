import logging

import pandas as pd

logger = logging.getLogger(__name__)


def aggregate_categorical_time_distribution(df: pd.DataFrame, categorical_col: str, freq: str = "YE") -> pd.DataFrame:
    """Aggregates a categorical column over time to show the distribution of classes per period.

    This transformation moves categorical values from rows to columns and counts their
    occurrences per time period, which is useful for stacked bar charts or trend analysis.

    Args:
        df: Input DataFrame with a DatetimeIndex.
        categorical_col: The name of the column containing categories (string or boolean).
        freq: Pandas frequency string (defaults to 'YE' for Year End).

    Returns:
        A DataFrame where the index represents time, columns represent categories
        plus a 'Total' column with the sum per period, and values represent counts.
        Returns an empty DataFrame if the input is empty, the column is missing
        or the aggregation fails.
    """
    if df.empty or categorical_col not in df.columns:
        return pd.DataFrame()

    try:
        # Group by Time Period AND Category to get raw counts
        # Unstack pivots the category level to columns
        counts = df.groupby([pd.Grouper(freq=freq), categorical_col]).size().unstack(fill_value=0)

        # Calculate total to facilitate percentage calculations downstream if needed
        counts["Total"] = counts.sum(axis=1)

        return counts
    except Exception as e:
        logger.error(f"Error aggregating categorical data: {e}", exc_info=True)
        return pd.DataFrame()
