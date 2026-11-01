from datetime import UTC, datetime


def calculate_cycle_boundaries(num_cycles: int = 30) -> list[datetime]:
    """Calculates theoretical solar cycle boundaries (minima).

    The calculation uses a linear approximation based on the average solar cycle
    length of 11.1 years, anchored at the solar minimum of 1810 (start of Cycle 6).
    This provides reference lines for visualization without requiring external
    catalog lookups.

    Args:
        num_cycles: Number of cycle boundaries to calculate, starting at 1810.

    Returns:
        The theoretical minima as timezone-aware UTC datetimes.
    """
    boundaries = []
    for cycle_index in range(num_cycles):
        decimal_year = 1810.6 + 11.1 * cycle_index
        year = int(decimal_year)
        # Convert decimal year fraction to approximate month
        month = int((decimal_year - year) * 12) + 1
        month = max(1, min(month, 12))
        boundaries.append(datetime(year, month, 1, tzinfo=UTC))
    return boundaries


def get_significant_events() -> list[datetime]:
    """Returns a fixed list of historically significant solar and geomagnetic storm events.

    Each event is dated to the first day of its month, as timezone-aware UTC datetime.
    """
    return [
        datetime(1859, 9, 1, tzinfo=UTC),
        datetime(1872, 2, 1, tzinfo=UTC),
        datetime(1882, 11, 1, tzinfo=UTC),
        datetime(1903, 10, 1, tzinfo=UTC),
        datetime(1909, 9, 1, tzinfo=UTC),
        datetime(1921, 5, 1, tzinfo=UTC),
        datetime(1938, 1, 1, tzinfo=UTC),
        datetime(1940, 3, 1, tzinfo=UTC),
        datetime(1941, 9, 1, tzinfo=UTC),
        datetime(1946, 3, 1, tzinfo=UTC),
        datetime(1956, 2, 1, tzinfo=UTC),
        datetime(1957, 9, 1, tzinfo=UTC),
        datetime(1958, 2, 1, tzinfo=UTC),
        datetime(1959, 7, 1, tzinfo=UTC),
        datetime(1967, 5, 1, tzinfo=UTC),
        datetime(1968, 10, 1, tzinfo=UTC),
        datetime(1972, 8, 1, tzinfo=UTC),
        datetime(1989, 3, 1, tzinfo=UTC),
        datetime(1989, 8, 1, tzinfo=UTC),
        datetime(1991, 11, 1, tzinfo=UTC),
        datetime(2000, 4, 1, tzinfo=UTC),
        datetime(2000, 7, 1, tzinfo=UTC),
        datetime(2001, 4, 1, tzinfo=UTC),
        datetime(2001, 11, 1, tzinfo=UTC),
        datetime(2003, 10, 1, tzinfo=UTC),
        datetime(2003, 11, 1, tzinfo=UTC),
        datetime(2005, 1, 1, tzinfo=UTC),
        datetime(2015, 3, 1, tzinfo=UTC),
        datetime(2017, 9, 1, tzinfo=UTC),
        datetime(2022, 2, 1, tzinfo=UTC),
        datetime(2024, 5, 1, tzinfo=UTC),
        datetime(2024, 10, 1, tzinfo=UTC),
        datetime(2025, 11, 1, tzinfo=UTC),
    ]
