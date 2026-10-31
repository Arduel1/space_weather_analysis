import pytest

from space_weather_analysis.util.naming import sanitize_collection_name


@pytest.mark.parametrize(
    ("dataset_id", "expected_name"),
    [
        pytest.param("valid_name", "valid_name", id="unchanged"),
        pytest.param("A/B-C/D-E", "A_B_C_D_E", id="slashes_and_hyphens"),
        pytest.param("", "", id="empty"),
    ],
)
def test_collection_name_replaces_slashes_and_hyphens(dataset_id: str, expected_name: str) -> None:
    """Slashes and hyphens of a dataset ID become underscores, other characters stay."""
    assert sanitize_collection_name(dataset_id) == expected_name
