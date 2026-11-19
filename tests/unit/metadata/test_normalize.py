import astropy.units as u
import pytest

from space_weather_analysis.metadata.normalize import normalize_units


@pytest.mark.parametrize(
    ("given", "meant"),
    [
        pytest.param("km s^-1", "km/s", id="standard-notation"),
        pytest.param("Km/s", "km/s", id="capital-unit-word"),
        pytest.param("Per cc", "cm^-3", id="per-prefix"),
        pytest.param("1/(SQcm-ster-s)", "1/(cm^2 s sr)", id="squared-and-hyphens"),
        pytest.param("MT", "MT", id="prefix-case-kept"),
    ],
)
def test_a_unit_astropy_reads_keeps_its_meaning(given: str, meant: str) -> None:
    """A unit is written uniformly, and the written unit is the same unit with the same factor as the one meant."""
    written = normalize_units(given)
    assert written is not None
    assert u.Unit(written) == u.Unit(meant)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param(" pfu ", "pfu", id="unknown-unit-kept"),
        pytest.param("electrons/(cm^2 s sr)", "electrons/(cm^2 s sr)", id="counted-thing-kept"),
        pytest.param("Dimensionless", None, id="no-unit"),
    ],
)
def test_a_unit_astropy_cannot_read_stays_as_given(given: str, expected: str | None) -> None:
    """Units astropy does not know keep the provider's text, texts naming no unit become None."""
    assert normalize_units(given) == expected
