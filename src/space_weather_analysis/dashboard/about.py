from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from space_weather_analysis.dashboard.labels import Metadata, variable_metadata
from space_weather_analysis.dashboard.validity import describe_validity, valid_range
from space_weather_analysis.metadata.models import MetadataSourceKind
from space_weather_analysis.metadata.spase.dictionary import spase_definition
from space_weather_analysis.metadata.spase.resources import SPASE_ID_PREFIX, spase_resource
from space_weather_analysis.util.time_utils import SecondsDuration, parse_iso_duration

# The model of every part is frozen, as it is only built and then serialized
FROZEN = ConfigDict(frozen=True)

# Readable page of a SPASE record (Space Physics Archive Search and Extract, https://spase-group.org/) with the resource
# ID `spase://{path}`: the Heliophysics Data Portal shows it at hpde.io under the same path
SPASE_PAGE_URL = "https://hpde.io/{path}"


class FieldAbout(BaseModel):
    """What the metadata record says about one shown field; a value the record lacks is None.

    Attributes:
        name: The field name.
        label: The short label.
        description: What the field holds.
        units: The unit of the values.
        validity: The fill value and valid range in words; None if neither is known.
        coordinate_system: The coordinate system of a vector component.
        flag_meanings: The meaning of each flag value.
    """

    model_config = FROZEN

    name: str
    label: str | None = None
    description: str | None = None
    units: str | None = None
    validity: str | None = None
    coordinate_system: str | None = None
    flag_meanings: dict[str, str] | None = None


class SpaseTermAbout(BaseModel):
    """A term of the SPASE dictionary with its definition.

    Attributes:
        term: The term as written in the SPASE record, such as Heliosphere.NearEarth.
        definition: The definition the SPASE dictionary gives; None if it is not available.
    """

    model_config = FROZEN

    term: str
    definition: str | None = None


class SourceAbout(BaseModel):
    """One source the metadata record was built from.

    Attributes:
        kind: The kind of source, such as curated, api or spase.
        url: The location the metadata was read from.
        retrieved_at: When the metadata was read.
        covers: Which values the source gave; None where it gave the whole record.
    """

    model_config = FROZEN

    kind: str
    url: str | None = None
    retrieved_at: datetime | None = None
    covers: str | None = None


class DatasetAbout(BaseModel):
    """What the metadata record says about one dataset of a panel, for a reader of the dashboard; a value the record lacks is None.

    Attributes:
        dataset: The dataset ID.
        title: The title.
        description: The description.
        observed_regions: The regions the data describe, as SPASE ObservedRegion terms with their definitions.
        measurement_types: The kinds of measurement, as SPASE MeasurementType terms with their definitions.
        spase_resource_id: The SPASE resource ID of the record describing the dataset or the same quantities.
        spase_url: The readable page of that SPASE record.
        provider: The organization providing the data.
        source_type: The kind of source the data are read from.
        resource_url: The landing page of the dataset at the provider.
        documentation: Further documentation pages.
        cadence: The time between two values.
        timestamp_location: Where in its measurement interval a time stamp lies.
        coverage_start: The first time the provider holds data for.
        coverage_stop: The last time the provider holds data for.
        license: The license name or URL.
        acknowledgement: The acknowledgement the provider requests.
        citation: The citation text.
        doi: The Digital Object Identifier of the dataset.
        rules_of_use: The terms of use text or URL.
        fields: The shown fields in the order of the panel.
        sources: The sources the record was built from.
    """

    model_config = FROZEN

    dataset: str
    title: str | None = None
    description: str | None = None
    observed_regions: list[SpaseTermAbout] = Field(default_factory=list)
    measurement_types: list[SpaseTermAbout] = Field(default_factory=list)
    spase_resource_id: str | None = None
    spase_url: str | None = None
    provider: str | None = None
    source_type: str | None = None
    resource_url: str | None = None
    documentation: list[str] = Field(default_factory=list)
    cadence: SecondsDuration | None = None
    timestamp_location: str | None = None
    coverage_start: datetime | None = None
    coverage_stop: datetime | None = None
    license: str | None = None
    acknowledgement: str | None = None
    citation: str | None = None
    doi: str | None = None
    rules_of_use: str | None = None
    fields: list[FieldAbout] = Field(default_factory=list)
    sources: list[SourceAbout] = Field(default_factory=list)


def _field_about(metadata: Metadata, field: str) -> FieldAbout:
    """Reads what the record says about one field."""
    variable = variable_metadata(metadata, field)
    validity = describe_validity(metadata, field) if valid_range(metadata, field).is_known else None
    return FieldAbout(
        name=field,
        label=variable.get("label"),
        description=variable.get("description"),
        units=variable.get("units"),
        validity=validity,
        coordinate_system=variable.get("coordinate_system"),
        flag_meanings=variable.get("flag_meanings"),
    )


def _spase_terms(terms: list[str]) -> list[SpaseTermAbout]:
    """Pairs each SPASE term of the record with its definition in the SPASE dictionary."""
    return [SpaseTermAbout(term=term, definition=spase_definition(term)) for term in terms]


def _spase_url(resource_id: str | None) -> str | None:
    """Builds the readable page of a SPASE record; None for a missing ID or one not of the form `spase://{path}`."""
    if not resource_id or not resource_id.startswith(SPASE_ID_PREFIX):
        return None
    return SPASE_PAGE_URL.format(path=resource_id.removeprefix(SPASE_ID_PREFIX).strip("/"))


def _source_about(entry: dict[str, Any]) -> SourceAbout:
    """Reads one provenance entry of the record."""
    return SourceAbout(kind=str(entry.get("kind")), url=entry.get("url"), retrieved_at=entry.get("retrieved_at"), covers=entry.get("covers"))


def dataset_about(dataset: str, metadata: Metadata, fields: list[str]) -> DatasetAbout | None:
    """Maps the metadata record of a dataset to what a reader of the dashboard sees about it.

    Args:
        dataset: The dataset ID.
        metadata: The metadata record as JSON-compatible dictionary; empty if the dataset has none.
        fields: The shown fields, in the order of the panel.

    Returns:
        The readable record; None without metadata record, as there is then nothing to show.
    """
    if not metadata:
        return None
    source = metadata.get("source", {})
    time_info = metadata.get("time", {})
    citation = metadata.get("citation", {})
    links = metadata.get("links", {})
    description = metadata.get("description")
    observed_regions = metadata.get("observed_regions") or []
    measurement_types = metadata.get("measurement_types") or []
    # A record naming a SPASE record whose terms it does not hold yet takes them from that record directly; a record
    # linked to the SPASE record of another dataset with the same quantity takes its terms but not its description
    resource_id = links.get("spase_resource_id")
    is_linked = any(entry.get("kind") == MetadataSourceKind.LINKED for entry in metadata.get("provenance", []))
    if resource_id and not (description and observed_regions and measurement_types):
        resource = spase_resource(resource_id)
        if resource is not None:
            description = description or (None if is_linked else resource.description)
            observed_regions = observed_regions or resource.observed_regions
            measurement_types = measurement_types or resource.measurement_types
    return DatasetAbout(
        dataset=dataset,
        title=metadata.get("title"),
        description=description,
        observed_regions=_spase_terms(observed_regions),
        measurement_types=_spase_terms(measurement_types),
        spase_resource_id=resource_id,
        spase_url=_spase_url(resource_id),
        provider=source.get("provider"),
        source_type=source.get("type"),
        resource_url=links.get("resource_url"),
        documentation=links.get("documentation") or [],
        cadence=parse_iso_duration(time_info.get("cadence") or ""),
        timestamp_location=time_info.get("timestamp_location"),
        coverage_start=time_info.get("coverage_start"),
        coverage_stop=time_info.get("coverage_stop"),
        license=citation.get("license"),
        acknowledgement=citation.get("acknowledgement"),
        citation=citation.get("text"),
        doi=citation.get("doi"),
        rules_of_use=citation.get("rules_of_use"),
        fields=[_field_about(metadata, field) for field in fields],
        sources=[_source_about(entry) for entry in metadata.get("provenance", [])],
    )
