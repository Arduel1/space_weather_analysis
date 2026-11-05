from typing import Any

import pytest

from space_weather_analysis.dashboard.labels import attribution, dataset_title, field_label, field_unit, sources_line

FULL_RECORD: dict[str, Any] = {
    "title": "GOES X-ray flux",
    "variables": [{"name": "flux", "label": "X-ray flux", "units": "W/m^2"}],
}


@pytest.mark.parametrize(
    ("configured", "metadata", "expected"),
    [
        pytest.param(("Flux", "Wm-2", "Own title"), FULL_RECORD, ("Flux", "Wm-2", "Own title"), id="configured-wins"),
        pytest.param(("", "", ""), FULL_RECORD, ("X-ray flux", "W/m^2", "GOES X-ray flux"), id="metadata-without-configuration"),
        pytest.param(("", "", ""), {"variables": [{"name": "other", "label": "Other"}]}, ("flux", "", ""), id="field-not-described"),
        pytest.param(("", "", ""), {}, ("flux", "", ""), id="no-metadata"),
    ],
)
def test_labels_prefer_configuration_then_metadata_then_field_name(
    configured: tuple[str, str, str], metadata: dict[str, Any], expected: tuple[str, str, str]
) -> None:
    """Label, unit and title come from the configuration, else the metadata, else the field name or nothing."""
    configured_label, configured_unit, configured_title = configured

    labels = (
        field_label(metadata, "flux", configured_label),
        field_unit(metadata, "flux", configured_unit),
        dataset_title(metadata, configured_title),
    )

    assert labels == expected


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        pytest.param(
            {"source": {"type": "SWPC", "provider": "Synthetic provider"}, "citation": {"license": "CC0", "doi": "10.0000/synthetic"}},
            "Synthetic provider, CC0, doi:10.0000/synthetic",
            id="provider-license-doi",
        ),
        pytest.param({"source": {"type": "HAPI", "server_url": "https://hapi.invalid/hapi"}}, "hapi.invalid", id="server-host-fallback"),
        pytest.param({"source": {"type": "HAPI"}}, "HAPI", id="source-type-fallback"),
        pytest.param({}, "no metadata", id="empty-record"),
    ],
)
def test_attribution_names_provider_and_terms(metadata: dict[str, Any], expected: str) -> None:
    """The attribution names the provider, else the server host, else the source type, followed by license and DOI."""
    assert attribution(metadata) == expected


def test_sources_line_names_each_attribution_once_in_order() -> None:
    """The source line lists each distinct attribution once, in the order of the records."""
    first = {"source": {"type": "SWPC", "provider": "First provider"}}
    second = {"source": {"type": "SWPC", "provider": "Second provider"}}

    assert sources_line([first, second, first, {}]) == "Source: First provider; Second provider; no metadata"
