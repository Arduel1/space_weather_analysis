from typing import Any, Optional

from space_weather_analysis.util.hapi_parameters import null_string_to_none

# Unit texts providers write for values without a unit or with an unknown unit, compared in lower case.
NO_UNIT_TEXTS = frozenset({"none", "unitless", "undefined", "dimensionless"})


def optional_text(value: Any) -> Optional[str]:
    """Converts a provider metadata value into text, keeping the text unchanged.

    Args:
        value: The value as given by the provider.

    Returns:
        The value as text, or None if it is missing, the string "null" or blank.
    """
    value = null_string_to_none(value)
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if not text.strip():
        return None
    return text


def normalize_units(value: Any) -> Optional[str]:
    """Normalizes a unit given by a provider.

    Args:
        value: The unit text as given by the provider.

    Returns:
        The unit without surrounding whitespace, or None if it is missing or names no unit
        ("null", blank, "none", "unitless", "undefined" or "dimensionless" in any letter case).
    """
    text = optional_text(value)
    if text is None:
        return None
    unit = text.strip()
    if unit.lower() in NO_UNIT_TEXTS:
        return None
    return unit
