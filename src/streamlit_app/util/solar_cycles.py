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
