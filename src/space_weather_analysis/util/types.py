from datetime import datetime
from typing import Tuple, TypeAlias

# --- Core Type Aliases ---

TimeRange: TypeAlias = Tuple[datetime, datetime]
"""
A tuple representing a time range: (start_time, end_time).
Both datetimes should be timezone-aware (UTC).
"""
