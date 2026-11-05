import math
from dataclasses import dataclass

import pandas as pd

from streamlit_app.dashboard.labels import Metadata, variable_metadata


@dataclass(frozen=True)
class ValidRange:
    """The values of a field that count as measurements.

    Attributes:
        fill: The value the provider writes for a missing measurement; None if it names none or none is a number.
        low: The smallest valid value; None without lower bound.
        high: The largest valid value; None without upper bound.
    """

    fill: float | None = None
    low: float | None = None
    high: float | None = None

    @property
    def is_known(self) -> bool:
        """Whether a fill value or a bound is known, so that hiding invalid values can change anything."""
        return self.fill is not None or self.low is not None or self.high is not None

    def is_valid(self, value: float) -> bool:
        """Tells whether one value is a measurement: no fill value and inside the bounds."""
        return bool(valid_mask(pd.Series([value], dtype=float), self).iloc[0])


def _number(value: object) -> float | None:
    """Reads a metadata value as finite number; None for text that is no number, for NaN and for missing values."""
    # bool is a subclass of int but no fill value or bound
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def valid_range(metadata: Metadata, field: str, low: float | None = None, high: float | None = None) -> ValidRange:
    """Combines the fill value and valid range of a field from its metadata with bounds given in the configuration.

    Args:
        metadata: The metadata record of the dataset.
        field: The data field.
        low: The smallest valid value from the configuration, taking precedence over the metadata; None for none.
        high: The largest valid value from the configuration, taking precedence over the metadata; None for none.

    Returns:
        The range; empty if neither source gives fill value or bounds.
    """
    variable = variable_metadata(metadata, field)
    return ValidRange(
        fill=_number(variable.get("fill")),
        low=low if low is not None else _number(variable.get("valid_min")),
        high=high if high is not None else _number(variable.get("valid_max")),
    )


def valid_mask(values: pd.Series, valid: ValidRange) -> pd.Series:
    """Marks the values that are measurements; missing values count as valid, as there is nothing to hide.

    Args:
        values: Numeric values.
        valid: The range of measurements.

    Returns:
        A boolean Series on the index of the values, False for the fill value and for values outside the bounds.
    """
    mask = pd.Series(True, index=values.index)
    if valid.fill is not None:
        mask &= values != valid.fill
    if valid.low is not None:
        mask &= ~(values < valid.low)
    if valid.high is not None:
        mask &= ~(values > valid.high)
    return mask


def describe_validity(metadata: Metadata, field: str, low: float | None = None, high: float | None = None) -> str:
    """Writes which values of a field count as no measurement, marking bounds that come from the configuration.

    Args:
        metadata: The metadata record of the dataset.
        field: The data field.
        low: The smallest valid value from the configuration; None for none.
        high: The largest valid value from the configuration; None for none.

    Returns:
        The fill value and the valid range, or a note that neither is known.
    """
    valid = valid_range(metadata, field, low, high)
    parts = []
    if valid.fill is not None:
        parts.append(f"fill {valid.fill:g}")
    low_text = f"{valid.low:g}{' (config)' if low is not None else ''}" if valid.low is not None else None
    high_text = f"{valid.high:g}{' (config)' if high is not None else ''}" if valid.high is not None else None
    if low_text is not None and high_text is not None:
        parts.append(f"valid {low_text} to {high_text}")
    elif low_text is not None:
        parts.append(f"valid >= {low_text}")
    elif high_text is not None:
        parts.append(f"valid <= {high_text}")
    return ", ".join(parts) or "no fill value or valid range known"
