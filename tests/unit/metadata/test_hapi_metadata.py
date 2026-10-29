from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pytest

from space_weather_analysis.metadata.hapi import build_hapi_metadata
from space_weather_analysis.metadata.models import DatasetMetadata, VariableMetadata
from space_weather_analysis.metadata.repository import sanitize_for_bson
from space_weather_analysis.util.hapi_parameters import parameter_columns

DATASET_ID = "synthetic/dataset-1"
COLLECTION = "synthetic_dataset_1"
SERVER_URL = "https://hapi.example.org/hapi"
RETRIEVED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
TIME_PARAMETER: Dict[str, Any] = {"name": "Time", "type": "isotime", "units": "UTC", "fill": "null", "length": 24}


def _info(*parameters: Dict[str, Any], **dataset_fields: Any) -> Dict[str, Any]:
    """Builds a minimal HAPI /info response with the time parameter first."""
    return {
        "HAPI": "3.1",
        "status": {"code": 1200, "message": "OK"},
        "startDate": "2024-01-01T00:00:00Z",
        "stopDate": "2024-02-01T00:00:00Z",
        "parameters": [TIME_PARAMETER, *parameters],
        **dataset_fields,
    }


def _build(info: Dict[str, Any]) -> DatasetMetadata:
    """Builds the metadata record of the synthetic dataset."""
    return build_hapi_metadata(DATASET_ID, COLLECTION, info, SERVER_URL, RETRIEVED_AT)


def _variables(info: Dict[str, Any]) -> Dict[str, VariableMetadata]:
    """Builds the record and returns its variables keyed by stored field name."""
    return {variable.name: variable for variable in _build(info).variables}


def _single_variable(**parameter_fields: Any) -> VariableMetadata:
    """Builds the record of an info with one scalar parameter and returns its only variable."""
    variables = _build(_info({"name": "value", "type": "double", **parameter_fields})).variables
    assert len(variables) == 1
    return variables[0]


class TestDatasetFields:
    def test_dataset_identity_source_and_provenance(self) -> None:
        """Dataset ID, collection, source type, server URL and one provenance entry of kind hapi are recorded."""
        record = _build(_info({"name": "value", "type": "double"}, modificationDate="2025-05-05T00:00Z"))

        assert (record.dataset_id, record.collection) == (DATASET_ID, COLLECTION)
        assert (record.source.type, record.source.server_url, record.source.provider) == ("HAPI", SERVER_URL, None)
        assert [(source.kind, source.url, source.retrieved_at, source.last_modified) for source in record.provenance] == [
            ("hapi", SERVER_URL, RETRIEVED_AT, "2025-05-05T00:00Z")
        ]

    def test_time_parameter_is_no_variable(self) -> None:
        """The first parameter becomes the document time stamp and is not listed as variable."""
        assert list(_variables(_info({"name": "value", "type": "double"}))) == ["value"]

    def test_coverage_dates_are_parsed_as_utc(self) -> None:
        """The HAPI startDate and stopDate are parsed as UTC times, including a date with trailing Z."""
        record = _build({**_info(), "startDate": "2016-12-31Z", "stopDate": "2024-02-01T06:30:00.5Z"})

        assert record.time.coverage_start == datetime(2016, 12, 31, tzinfo=timezone.utc)
        assert record.time.coverage_stop == datetime(2024, 2, 1, 6, 30, 0, 500000, tzinfo=timezone.utc)

    @pytest.mark.parametrize("empty_date", ["", "null"])
    def test_empty_dates_become_none(self, empty_date: str) -> None:
        """An empty or "null" date leaves the coverage bound unset."""
        record = _build({**_info(), "startDate": empty_date, "stopDate": empty_date})

        assert (record.time.coverage_start, record.time.coverage_stop) == (None, None)

    @pytest.mark.parametrize(("given", "expected"), [("BEGIN", "begin"), ("End", "end"), (None, "center"), ("middle", "other")])
    def test_timestamp_location_lower_cased_with_center_default(self, given: Optional[str], expected: str) -> None:
        """The HAPI timeStampLocation is lower-cased, 'center' if absent and 'other' outside the HAPI vocabulary."""
        info = _info() if given is None else _info(timeStampLocation=given)

        assert _build(info).time.timestamp_location == expected

    def test_cadence_title_description_and_resource_url_are_kept(self) -> None:
        """cadence, title, description and resourceURL are kept unchanged."""
        record = _build(_info(cadence="PT1M", title="Synthetic title", description="  Text as given. ", resourceURL="https://example.org/ds"))

        assert (record.time.cadence, record.title, record.description, record.links.resource_url) == (
            "PT1M",
            "Synthetic title",
            "  Text as given. ",
            "https://example.org/ds",
        )

    def test_dataset_citation_preferred_over_citation(self) -> None:
        """The HAPI datasetCitation is used as citation text when both datasetCitation and citation are given."""
        record = _build(_info(datasetCitation="Dataset citation", citation="Generic citation"))

        assert record.citation.text == "Dataset citation"

    def test_citation_used_without_dataset_citation(self) -> None:
        """The HAPI citation is used as citation text when datasetCitation is absent."""
        assert _build(_info(citation="Generic citation")).citation.text == "Generic citation"

    @pytest.mark.parametrize(
        ("license_url", "expected"),
        [
            ("https://spdx.org/licenses/CC-BY-4.0", "https://spdx.org/licenses/CC-BY-4.0"),
            (["https://a.example", "https://b.example"], "https://a.example, https://b.example"),
        ],
    )
    def test_license_url_becomes_license(self, license_url: Any, expected: str) -> None:
        """licenseURL, a single URL or a list of URLs, becomes the license text."""
        assert _build(_info(licenseURL=license_url)).citation.license == expected

    def test_spase_resource_id_is_stored(self) -> None:
        """A resourceID starting with spase:// becomes the SPASE resource ID and no DOI."""
        record = _build(_info(resourceID="spase://Synthetic/NumericalData/Example"))

        assert (record.links.spase_resource_id, record.citation.doi) == ("spase://Synthetic/NumericalData/Example", None)

    @pytest.mark.parametrize("resource_id", ["10.5880/Synthetic.1", "https://doi.org/10.5880/Synthetic.1", "doi:10.5880/Synthetic.1"])
    def test_doi_resource_id_becomes_doi(self, resource_id: str) -> None:
        """A resourceID that is a DOI, bare, as doi.org URL or with doi: prefix, becomes the bare DOI."""
        record = _build(_info(resourceID=resource_id))

        assert (record.citation.doi, record.links.spase_resource_id) == ("10.5880/Synthetic.1", None)

    def test_doi_citation_becomes_doi(self) -> None:
        """A citation that consists of a DOI also becomes the bare DOI when the resourceID gives none."""
        record = _build(_info(citation="https://doi.org/10.5880/Synthetic.2"))

        assert (record.citation.doi, record.citation.text) == ("10.5880/Synthetic.2", "https://doi.org/10.5880/Synthetic.2")

    def test_other_resource_id_is_ignored(self) -> None:
        """A resourceID that is neither SPASE ID nor DOI, as KNMI writes provider names there, is not stored."""
        record = _build(_info(resourceID="Synthetic Provider Agency"))

        assert (record.citation.doi, record.links.spase_resource_id) == (None, None)

    def test_contact_is_ignored(self) -> None:
        """The contact of the info appears nowhere in the record."""
        record = _build(_info(contact="Jane Doe @ Example"))

        assert "Jane Doe" not in str(record.model_dump())

    def test_info_without_parameters_is_rejected(self) -> None:
        """An info without parameters raises ValueError."""
        with pytest.raises(ValueError):
            _build({"HAPI": "3.1", "startDate": "2024-01-01T00:00:00Z"})


class TestVariableFields:
    def test_null_strings_become_none(self) -> None:
        """The string "null" in description, units, fill and coordinate system becomes None."""
        variable = _single_variable(description="null", units="null", fill="null", coordinateSystemName="null")

        assert (variable.description, variable.units, variable.fill, variable.coordinate_system) == (None, None, None, None)

    @pytest.mark.parametrize("no_unit", ["", " ", "None", "none", "unitless", "Unitless", "undefined", "Dimensionless", "dimensionless"])
    def test_texts_naming_no_unit_become_none(self, no_unit: str) -> None:
        """Unit texts that name no unit are stored as None."""
        assert _single_variable(units=no_unit).units is None

    def test_unit_is_stripped(self) -> None:
        """A real unit is kept without surrounding whitespace."""
        assert _single_variable(units=" nT ").units == "nT"

    def test_fill_kept_as_given(self) -> None:
        """A fill value is kept as the provider gives it."""
        assert _single_variable(fill="-1.0E31").fill == "-1.0E31"

    @pytest.mark.parametrize(("hapi_type", "expected"), [("float", "double"), ("double", "double"), ("integer", "integer"), ("string", "string")])
    def test_type_becomes_dtype(self, hapi_type: str, expected: str) -> None:
        """The HAPI type becomes the dtype, with float read as double."""
        assert _single_variable(type=hapi_type).dtype == expected

    def test_scalar_vector_component_string(self) -> None:
        """A vectorComponents string of a scalar parameter becomes its vector component."""
        assert _single_variable(vectorComponents="r").vector_component == "r"

    def test_vector_component_outside_vocabulary_is_dropped(self) -> None:
        """A vector component outside the HAPI 3.1 vocabulary is not stored."""
        assert _single_variable(vectorComponents="north").vector_component is None

    def test_field_sources_name_hapi_for_every_set_field(self) -> None:
        """field_sources holds kind hapi for exactly the fields that have a value."""
        variable = _single_variable(units="nT", description="Synthetic value")

        assert variable.field_sources == {"name": "hapi", "source_name": "hapi", "description": "hapi", "dtype": "hapi", "units": "hapi"}


class TestArrayParameters:
    def test_intermagnet_like_vector_with_labels_unit_array_and_coordinate_system(self) -> None:
        """A vector with labels, unit array, vectorComponents and coordinateSystemName yields one variable per labeled component."""
        parameter = {
            "name": "Field_Vector",
            "type": "double",
            "size": [3],
            "units": ["nT", "nT", "deg"],
            "label": ["X", "Y", "D"],
            "vectorComponents": ["x", "y", "longitude"],
            "coordinateSystemName": "GEO",
            "fill": "99999",
        }

        variables = _variables(_info(parameter))

        assert list(variables) == ["Field_Vector_X", "Field_Vector_Y", "Field_Vector_D"]
        described = [
            (
                variable.source_name,
                variable.component,
                variable.label,
                variable.units,
                variable.vector_component,
                variable.coordinate_system,
                variable.fill,
            )
            for variable in variables.values()
        ]
        assert described == [
            ("Field_Vector", "X", "X", "nT", "x", "GEO", "99999"),
            ("Field_Vector", "Y", "Y", "nT", "y", "GEO", "99999"),
            ("Field_Vector", "D", "D", "deg", "longitude", "GEO", "99999"),
        ]

    def test_iswa_like_vector_without_labels_uses_indices(self) -> None:
        """A vector without labels yields `{p}_{index}` variables with the index as component and the single unit for all."""
        parameter = {"name": "B_GSM", "type": "double", "size": [3], "units": "nT", "fill": "-9999", "description": "Field"}

        variables = _variables(_info(parameter))

        assert [(name, variable.component, variable.units, variable.label) for name, variable in variables.items()] == [
            ("B_GSM_0", 0, "nT", None),
            ("B_GSM_1", 1, "nT", None),
            ("B_GSM_2", 2, "nT", None),
        ]

    def test_knmi_like_vector_with_single_label_applies_it_to_every_component(self) -> None:
        """A single label string of a vector applies to every component, whose names use indices."""
        parameter = {"name": "pos", "type": "double", "size": [2], "units": "km", "label": "Position"}

        assert [(name, variable.label) for name, variable in _variables(_info(parameter)).items()] == [("pos_0", "Position"), ("pos_1", "Position")]

    def test_unit_array_of_wrong_length_is_not_applied(self) -> None:
        """A unit array whose length differs from the vector size leaves the units unset."""
        parameter = {"name": "v", "type": "double", "size": [3], "units": ["km", "km"]}

        assert [variable.units for variable in _variables(_info(parameter)).values()] == [None, None, None]

    def test_matrix_elements_are_named_by_both_indices(self) -> None:
        """A 2x2 matrix yields `{p}_{i}_{j}` variables in C order with the flat index as component and per-element units."""
        parameter = {"name": "m", "type": "double", "size": [2, 2], "units": [["a", "b"], ["c", "d"]]}

        variables = _variables(_info(parameter))

        assert [(name, variable.component, variable.units) for name, variable in variables.items()] == [
            ("m_0_0", 0, "a"),
            ("m_0_1", 1, "b"),
            ("m_1_0", 2, "c"),
            ("m_1_1", 3, "d"),
        ]

    def test_arrays_above_column_limit_go_to_not_stored(self) -> None:
        """Arrays with more than 10 elements yield no variables and are listed as not stored with a reason."""
        record = _build(
            _info(
                {"name": "kept", "type": "double", "size": [10]},
                {"name": "spectrum", "type": "double", "size": [11]},
                {"name": "image", "type": "double", "size": [3, 4]},
            )
        )

        assert {variable.source_name for variable in record.variables} == {"kept"}
        assert [(skipped.name, skipped.reason) for skipped in record.not_stored] == [
            ("spectrum", "array with more than 10 elements"),
            ("image", "array with more than 10 elements"),
        ]

    @pytest.mark.parametrize(
        "parameter",
        [
            {"name": "s", "type": "double"},
            {"name": "v", "type": "double", "size": [3], "label": ["x", "y", "z"]},
            {"name": "w", "type": "double", "size": [2], "label": ["only_one"]},
            {"name": "m", "type": "double", "size": [2, 3]},
        ],
    )
    def test_variable_names_equal_ingestion_column_names(self, parameter: Dict[str, Any]) -> None:
        """The variable names equal the column names the HAPI ingestion stores."""
        expected_names: List[str] = [column.name for column in parameter_columns(parameter)]

        assert list(_variables(_info(parameter))) == expected_names


class TestDocumentRoundTrip:
    def test_record_survives_document_round_trip(self) -> None:
        """A record converted to a document and back equals the original, with the dataset ID as _id."""
        record = _build(
            _info(
                {"name": "B", "type": "double", "size": [3], "units": "nT", "label": ["x", "y", "z"], "vectorComponents": ["x", "y", "z"]},
                {"name": "flag", "type": "integer", "fill": "-1"},
                {"name": "spectrum", "type": "double", "size": [20]},
                datasetCitation="Citation",
                resourceID="spase://Synthetic/Example",
            )
        )

        document = record.to_document()

        assert document["_id"] == DATASET_ID
        assert DatasetMetadata.from_document(document) == record

    def test_naive_stored_times_are_read_as_utc(self) -> None:
        """Times read back from MongoDB without time zone are read as UTC."""
        document = _build(_info()).to_document()
        document["updated_at"] = RETRIEVED_AT.replace(tzinfo=None)

        assert DatasetMetadata.from_document(document).updated_at == RETRIEVED_AT


class TestSanitizeForBson:
    def test_numpy_values_nan_and_bytes_are_converted(self) -> None:
        """NumPy arrays and scalars become Python values, NaN becomes None and bytes become text."""
        payload = {"array": np.array([1.5, np.nan]), "integer": np.int32(7), "flag": np.bool_(True), "text": b"abc", 3: "key"}

        assert sanitize_for_bson(payload) == {"array": [1.5, None], "integer": 7, "flag": True, "text": "abc", "3": "key"}
