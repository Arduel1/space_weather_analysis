from datetime import datetime
from typing import List


def calculate_cycle_boundaries(num_cycles: int = 30) -> List[datetime]:
    """
    Calculates theoretical solar cycle boundaries (minima).

    The calculation uses a linear approximation based on the average solar cycle
    length of 11.1 years, anchored at the solar minimum of 1810 (start of Cycle 6).
    This provides reference lines for visualization without requiring external
    catalog lookups.

    Args:
        num_cycles: Number of future cycles to calculate.

    Returns:
        A list of datetime objects representing the theoretical minima.
    """
    boundaries = []
    for i in range(num_cycles):
        timepoint = 1810.6 + 11.1 * i
        year = int(timepoint)
        # Convert decimal year fraction to approximate month
        month = int((timepoint - year) * 12) + 1
        month = max(1, min(month, 12))

        dt = datetime(year, month, 1)
        boundaries.append(dt)
    return boundaries


def get_significant_events() -> List[datetime]:
    """
    Returns a list of dates representing significant solar events (e.g. major storms).
    """
    return [
        datetime(1859, 9, 1),
        datetime(1872, 2, 1),
        datetime(1882, 11, 1),
        datetime(1903, 10, 1),
        datetime(1909, 9, 1),
        datetime(1921, 5, 1),
        datetime(1938, 1, 1),
        datetime(1940, 3, 1),
        datetime(1941, 9, 1),
        datetime(1946, 3, 1),
        datetime(1956, 2, 1),
        datetime(1957, 9, 1),
        datetime(1958, 2, 1),
        datetime(1959, 7, 1),
        datetime(1967, 5, 1),
        datetime(1968, 10, 1),
        datetime(1972, 8, 1),
        datetime(1989, 3, 1),
        datetime(1989, 8, 1),
        datetime(1991, 11, 1),
        datetime(2000, 4, 1),
        datetime(2000, 7, 1),
        datetime(2001, 4, 1),
        datetime(2001, 11, 1),
        datetime(2003, 10, 1),
        datetime(2003, 11, 1),
        datetime(2005, 1, 1),
        datetime(2015, 3, 1),
        datetime(2017, 9, 1),
        datetime(2022, 2, 1),
        datetime(2024, 5, 1),
        datetime(2024, 10, 1),
        datetime(2025, 11, 1),
    ]
