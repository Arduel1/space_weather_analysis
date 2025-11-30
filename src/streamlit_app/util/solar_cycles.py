from datetime import datetime
from typing import List


def calculate_cycle_boundaries(num_cycles: int = 30) -> List[datetime]:
    """
    Calculates theoretical solar cycle boundaries (minima).

    Based on the linear formula: timepoint = 1810.6 + 11.1 * i
    This approximation assumes an average cycle length of 11.1 years,
    anchored at the minimum of 1810 (start of Cycle 6).

    Args:
        num_cycles: Number of cycles to calculate into the future.

    Returns:
        List of datetime objects representing the theoretical minima.
    """
    boundaries = []
    for i in range(num_cycles):
        timepoint = 1810.6 + 11.1 * i
        year = int(timepoint)
        # Calculate month from the decimal part (0.1 years approx 1.2 months)
        month = int((timepoint - year) * 12) + 1
        month = max(1, min(month, 12))

        dt = datetime(year, month, 1)
        boundaries.append(dt)
    return boundaries
