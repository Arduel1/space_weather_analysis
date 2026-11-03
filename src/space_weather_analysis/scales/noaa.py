from enum import IntEnum

import numpy as np
import pandas as pd

# Source of all thresholds below: https://www.swpc.noaa.gov/noaa-scales-explanation


class GLevel(IntEnum):
    """NOAA geomagnetic storm level, G0 is below the scale."""

    G0 = 0
    G1 = 1
    G2 = 2
    G3 = 3
    G4 = 4
    G5 = 5


class SLevel(IntEnum):
    """NOAA solar radiation storm level, S0 is below the scale."""

    S0 = 0
    S1 = 1
    S2 = 2
    S3 = 3
    S4 = 4
    S5 = 5


class RLevel(IntEnum):
    """NOAA radio blackout level, R0 is below the scale."""

    R0 = 0
    R1 = 1
    R2 = 2
    R3 = 3
    R4 = 4
    R5 = 5


# Severity name of each level number, shared by the G, S and R scales.
LEVEL_NAMES: dict[int, str] = {0: "None", 1: "Minor", 2: "Moderate", 3: "Strong", 4: "Severe", 5: "Extreme"}

# Lowest Kp of G1 to G5 in thirds of a Kp unit. Kp (planetary K index) runs from 0 to 9 in steps of one
# third, written 5- = 4 2/3, 5o = 5 and 5+ = 5 1/3. NOAA: G1 Kp = 5, G2 Kp = 6, G3 Kp = 7,
# G4 "Kp = 8, including a 9-", G5 Kp = 9, so 5- starts G1 and only 9o reaches G5.
G_THRESHOLDS_KP_THIRDS = np.array([14, 17, 20, 23, 27])
# Lowest Kp of G1 to G5 as Kp values, for threshold lines.
G_THRESHOLDS_KP = G_THRESHOLDS_KP_THIRDS / 3
# Lowest >=10 MeV integral proton flux of S1 to S5 in pfu (particle flux unit, protons / (cm^2 s sr)).
S_THRESHOLDS_PFU = np.array([1e1, 1e2, 1e3, 1e4, 1e5])
# Lowest GOES 0.1-0.8 nm X-ray flux of R1 to R5 in W/m^2: flare classes M1, M5, X1, X10, X20.
R_THRESHOLDS_W_M2 = np.array([1e-5, 5e-5, 1e-4, 1e-3, 2e-3])
# Lowest 0.1-0.8 nm X-ray flux of the flare classes B, C, M and X in W/m^2; class A covers everything below.
FLARE_CLASS_THRESHOLDS_W_M2 = np.array([1e-7, 1e-6, 1e-5, 1e-4])
# Letter of each flare class, ordered by flux.
FLARE_CLASS_LETTERS = np.array(["A", "B", "C", "M", "X"])
# Lowest flux of each flare class letter in W/m^2, the unit of the class magnitude.
FLARE_CLASS_BASES_W_M2 = np.array([1e-8, 1e-7, 1e-6, 1e-5, 1e-4])
# Absorbs float error so that 2.3e-6 / 1e-6 gives magnitude 2.3 and not 2.2.
_MAGNITUDE_EPSILON = 1e-9


def _to_float_array(values: pd.Series) -> np.ndarray:
    """Converts values to a float array, missing and non-numeric values as NaN."""
    return pd.to_numeric(values, errors="coerce").to_numpy(dtype=float, na_value=np.nan)


def _level_series(values: np.ndarray, index: pd.Index, thresholds: np.ndarray) -> pd.Series:
    """Classifies values into the levels of one scale.

    Args:
        values: Float values, NaN where missing.
        index: Index of the returned Series.
        thresholds: Ascending lowest values of levels 1 to n.

    Returns:
        A nullable integer Series of the number of thresholds each value reaches, a value equal to a
        threshold reaching that level, NA for missing values.
    """
    numbers = np.searchsorted(thresholds, values, side="right")
    return pd.Series(numbers, index=index, dtype="Int64").mask(~np.isfinite(values))


def g_level(kp: pd.Series) -> pd.Series:
    """Classifies Kp values into the NOAA geomagnetic storm scale.

    Kp is rounded to the nearest third first, so stored values like 4.67 or 4.666 both count as 5-.

    Args:
        kp: Kp values.

    Returns:
        A nullable integer Series of GLevel numbers on the index of kp, NA for missing values.
    """
    return _level_series(np.round(_to_float_array(kp) * 3), kp.index, G_THRESHOLDS_KP_THIRDS)


def s_level(proton_flux_pfu: pd.Series) -> pd.Series:
    """Classifies GOES >=10 MeV integral proton flux into the NOAA solar radiation storm scale.

    GOES are the NOAA Geostationary Operational Environmental Satellites.

    Args:
        proton_flux_pfu: Flux values in pfu.

    Returns:
        A nullable integer Series of SLevel numbers on the index of the input, NA for missing values.
    """
    return _level_series(_to_float_array(proton_flux_pfu), proton_flux_pfu.index, S_THRESHOLDS_PFU)


def r_level(xray_flux_w_m2: pd.Series) -> pd.Series:
    """Classifies GOES 0.1-0.8 nm X-ray flux into the NOAA radio blackout scale.

    Args:
        xray_flux_w_m2: Flux values in W/m^2.

    Returns:
        A nullable integer Series of RLevel numbers on the index of the input, NA for missing values.
    """
    return _level_series(_to_float_array(xray_flux_w_m2), xray_flux_w_m2.index, R_THRESHOLDS_W_M2)


def flare_class(xray_flux_w_m2: pd.Series) -> pd.Series:
    """Names the GOES flare class of 0.1-0.8 nm X-ray flux values.

    The class is the letter of the decade and the flux in units of the decade start, cut to one decimal
    as GOES does: 5.27e-5 W/m^2 is M5.2, 2.1e-3 W/m^2 is X21.0.

    Args:
        xray_flux_w_m2: Flux values in W/m^2.

    Returns:
        An object Series of classes on the index of the input, None for missing or non-positive values.
    """
    flux = _to_float_array(xray_flux_w_m2)
    is_valid = np.isfinite(flux) & (flux > 0)
    class_index = np.searchsorted(FLARE_CLASS_THRESHOLDS_W_M2, flux, side="right")
    magnitude = np.floor(flux / FLARE_CLASS_BASES_W_M2[class_index] * 10 + _MAGNITUDE_EPSILON) / 10
    names = np.char.add(FLARE_CLASS_LETTERS[class_index], np.char.mod("%.1f", np.where(is_valid, magnitude, 0.0)))
    classes = pd.Series(names, index=xray_flux_w_m2.index, dtype=object)
    return classes.where(is_valid, None)
