import functools
import re
import warnings
from typing import Any

import astropy.units as u

from space_weather_analysis.util.hapi_parameters import null_string_to_none

# Unit texts providers write for values without a unit or with an unknown unit, compared in lower case.
NO_UNIT_TEXTS = frozenset({"none", "unitless", "undefined", "dimensionless"})

# Unit words of older provider notations that astropy does not know, with the unit each one means
UNIT_WORD_ALIASES = {"Km": u.km, "sec": u.s, "ster": u.sr, "cc": u.cm**3, "degrees": u.deg}

# "Per {unit}" at the start, as in "Per cc", means the inverse of the unit
PER_PREFIX = re.compile(r"^Per\s+")
# "SQ" before a unit word, as in "SQcm", means the unit squared
SQUARED_UNIT = re.compile(r"SQ([A-Za-z]+)")
# A hyphen before a letter separates two units, as in "cm^2-ster-s"; a hyphen before a digit is a negative exponent
# ("s-1") and stays
UNIT_SEPARATOR_HYPHEN = re.compile(r"-(?=[A-Za-z])")
# A unit word: a run of letters, as in "km" or "sr" of "km/(s sr)"
UNIT_WORD = re.compile(r"[A-Za-z]+")


def optional_text(value: Any) -> str | None:
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


def optional_texts(value: Any) -> list[str]:
    """Converts a repeated provider metadata value into the list of its texts.

    Args:
        value: The value as given by the provider: one value, a list of values or None.

    Returns:
        The entries as text in their order, leaving out those optional_text finds missing.
    """
    if value is None:
        return []
    entries = value if isinstance(value, list) else [value]
    return [text for text in (optional_text(entry) for entry in entries) if text]


def normalize_units(value: Any) -> str | None:
    """Normalizes a unit given by a provider.

    Args:
        value: The unit text as given by the provider.

    Returns:
        The unit in the notation of astropy if astropy can read it, else as given without surrounding whitespace; None if
        it is missing or names no unit ("null", blank, "none", "unitless", "undefined" or "dimensionless" in any letter case).
    """
    text = optional_text(value)
    if text is None:
        return None
    unit = text.strip()
    if unit.lower() in NO_UNIT_TEXTS:
        return None
    return _uniform_unit(unit)


@functools.cache
def _uniform_unit(unit: str) -> str:
    """Writes a unit as astropy does, so that every spelling of one unit reads the same; a unit astropy cannot read stays as given.

    Providers write one unit in many ways ("km/s", "km s^-1", "Km/s"). astropy reads the unit and writes it again, which
    keeps its meaning, as both are the same unit with the same factor. Cached, as each unit text is read many times.
    """
    readable = UNIT_SEPARATOR_HYPHEN.sub(" ", SQUARED_UNIT.sub(r"\1^2", PER_PREFIX.sub("1/", unit)))
    try:
        # astropy warns about notations it reads with doubt; such a unit is rejected by parse_strict as well
        with warnings.catch_warnings(), u.add_enabled_aliases(UNIT_WORD_ALIASES):
            warnings.simplefilter("ignore")
            parsed = u.Unit(readable, parse_strict="raise")
    except ValueError:
        return unit
    # astropy also reads words the provider may mean otherwise ("b" as bit); the reading is kept only if each unit it
    # found is written in the text, directly or through an alias
    words = set(UNIT_WORD.findall(readable))
    written = words | {base.to_string() for word in words & UNIT_WORD_ALIASES.keys() for base in UNIT_WORD_ALIASES[word].bases}
    if any(base.to_string() not in written for base in parsed.bases):
        return unit
    return parsed.to_string("unicode")
