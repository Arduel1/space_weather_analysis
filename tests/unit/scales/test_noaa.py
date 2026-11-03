import numpy as np
import pandas as pd

from space_weather_analysis.scales.noaa import GLevel, RLevel, SLevel, flare_class, g_level, r_level, s_level


def test_g_level_boundaries_in_kp_thirds() -> None:
    """Kp 5- starts G1, each minus third starts the next level, 9- stays G4 and only 9 is G5."""
    kp = pd.Series([4 + 1 / 3, 4.67, 5 + 1 / 3, 5 + 2 / 3, 6 + 2 / 3, 7 + 1 / 3, 7 + 2 / 3, 8.667, 9.0])
    expected = [GLevel.G0, GLevel.G1, GLevel.G1, GLevel.G2, GLevel.G3, GLevel.G3, GLevel.G4, GLevel.G4, GLevel.G5]

    assert g_level(kp).tolist() == expected


def test_s_and_r_level_boundaries() -> None:
    """A flux equal to a threshold reaches that level, a flux just below stays one level lower."""
    proton_flux = pd.Series([9.99, 10, 100, 1e3, 1e4, 99_999, 1e5])
    xray_flux = pd.Series([9.9e-6, 1e-5, 5e-5, 1e-4, 1e-3, 1.99e-3, 2e-3])

    assert s_level(proton_flux).tolist() == [SLevel.S0, SLevel.S1, SLevel.S2, SLevel.S3, SLevel.S4, SLevel.S4, SLevel.S5]
    assert r_level(xray_flux).tolist() == [RLevel.R0, RLevel.R1, RLevel.R2, RLevel.R3, RLevel.R4, RLevel.R4, RLevel.R5]


def test_flare_class_names_and_missing_values() -> None:
    """Each decade gets its letter with the magnitude cut to one decimal; missing values stay missing."""
    flux = pd.Series([5e-9, 1e-7, 2.3e-6, 9.99e-6, 1e-5, 5.27e-5, 1e-4, 2.1e-3, np.nan, 0.0], index=list("abcdefghij"))

    classes = flare_class(flux)

    assert classes.tolist() == ["A0.5", "B1.0", "C2.3", "C9.9", "M1.0", "M5.2", "X1.0", "X21.0", None, None]
    assert classes.index.equals(flux.index)


def test_levels_keep_series_index_and_mark_missing_values() -> None:
    """A level Series is nullable integer on the index of the input, with NA where the value is missing."""
    kp = pd.Series([3.0, None, 9.0], index=pd.date_range("2024-05-10", periods=3, freq="3h", tz="UTC"))

    levels = g_level(kp)

    assert levels.dtype == "Int64"
    assert levels.index.equals(kp.index)
    assert levels.isna().tolist() == [False, True, False]
    assert levels.dropna().tolist() == [0, 5]
