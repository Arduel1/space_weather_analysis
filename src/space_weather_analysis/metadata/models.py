from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.util.time_utils import UtcDatetime


class MetadataSourceKind(StrEnum):
    """Where a metadata value comes from; the members are stored as their lower-case values."""

    # The /info response of a HAPI server (Heliophysics Application Programmer's Interface, https://hapi-server.org/).
    HAPI = "hapi"
    # The CDAWeb master file of a dataset, its authoritative attributes following the ISTP Metadata Guidelines
    # (International Solar-Terrestrial Physics, https://istp-metadata.readthedocs.io/).
    ISTP = "istp"
    # A SPASE record (Space Physics Archive Search and Extract, https://spase-group.org/).
    SPASE = "spase"
    # A value written in the code of this project.
    CURATED = "curated"
    # The attributes delivered with the data, which the master file of the dataset overrides.
    FILE = "file"
    # The response of a provider API other than HAPI.
    API = "api"
    # The dataset list of CDAS, the CDAWeb Coordinated Data Analysis System web services (https://cdaweb.gsfc.nasa.gov/WebServices/REST/).
    CDAS = "cdas"


class DataType(StrEnum):
    """The value type of a stored field, named like the HAPI parameter types."""

    DOUBLE = "double"
    INTEGER = "integer"
    STRING = "string"
    ISOTIME = "isotime"
    BOOLEAN = "boolean"


class TimestampLocation(StrEnum):
    """The position of a time stamp in its measurement interval, the vocabulary of the HAPI 'timeStampLocation'."""

    BEGIN = "begin"
    CENTER = "center"
    END = "end"
    OTHER = "other"


class VectorComponent(StrEnum):
    """The meaning of one element of a vector, the vocabulary of the HAPI 3.1 'vectorComponents'."""

    X = "x"
    Y = "y"
    Z = "z"
    R = "r"
    RHO = "rho"
    LATITUDE = "latitude"
    COLATITUDE = "colatitude"
    LONGITUDE = "longitude"
    LONGITUDE0 = "longitude0"
    ALTITUDE = "altitude"
    OTHER = "other"


# Arrays with more elements are maps, images or spectra rather than single values over time, so they are not stored as columns.
MAX_ARRAY_COLUMNS = 10


class NotStoredReason(StrEnum):
    """Why a provider parameter is left out of the data documents."""

    TOO_MANY_ELEMENTS = f"array with more than {MAX_ARRAY_COLUMNS} elements"
    # The first dimension of the variable is no time coordinate, so its values cannot be placed in time-stamped documents.
    TIME_AXIS_NOT_RESOLVABLE = "time axis not resolvable"
    # The variable has no value per record; it is a constant of the dataset, not a series over time.
    NOT_TIME_VARYING = "not time-varying"


class SourceInfo(BaseModel):
    """The system a dataset is ingested from.

    Attributes:
        type: The client family that ingests the dataset.
        provider: The organization providing the data, as named by the provider.
        server_url: The base URL of the server the data comes from.
    """

    type: SourceType
    provider: str | None = None
    server_url: str | None = None


class TimeInfo(BaseModel):
    """The time axis of a dataset.

    Attributes:
        coverage_start: The time of the first available record.
        coverage_stop: The time of the last available record.
        cadence: The time between records as ISO 8601 duration.
        timestamp_location: The position of each time stamp in its measurement interval.
    """

    coverage_start: UtcDatetime | None = None
    coverage_stop: UtcDatetime | None = None
    cadence: str | None = None
    timestamp_location: TimestampLocation | None = None


class CitationInfo(BaseModel):
    """How a dataset is cited and under which terms it may be used.

    Attributes:
        doi: The Digital Object Identifier of the dataset, without resolver prefix.
        text: The citation text given by the provider.
        acknowledgement: The acknowledgement text requested by the provider.
        license: The license name or license URL.
        rules_of_use: The terms of use text or URL of the provider.
    """

    doi: str | None = None
    text: str | None = None
    acknowledgement: str | None = None
    license: str | None = None
    rules_of_use: str | None = None


class LinkInfo(BaseModel):
    """References to descriptions of a dataset outside this project.

    Attributes:
        resource_url: The landing page of the dataset at the provider.
        spase_resource_id: The SPASE resource ID (Space Physics Archive Search and Extract, https://spase-group.org/).
        documentation: URLs of further documentation.
    """

    resource_url: str | None = None
    spase_resource_id: str | None = None
    documentation: list[str] = Field(default_factory=list)


class SkippedParameter(BaseModel):
    """A provider parameter that is not stored in the data documents.

    Attributes:
        name: The name of the parameter at the provider.
        reason: Why the parameter is not stored.
    """

    name: str
    reason: NotStoredReason


class MetadataSource(BaseModel):
    """One source the metadata record was built from.

    Attributes:
        kind: The kind of source.
        url: The location the metadata was read from.
        retrieved_at: When the metadata was read.
        last_modified: The modification time the source reports, as given by the source.
    """

    kind: MetadataSourceKind
    url: str | None = None
    retrieved_at: UtcDatetime
    last_modified: str | None = None


class VariableMetadata(BaseModel):
    """The description of one field stored in the data documents.

    Attributes:
        name: The field name in the stored documents.
        source_name: The name of the variable at the provider.
        component: The label or index of the array element this field holds; the flat index for arrays with more than one dimension.
        element_index: The flat index of the array element this field holds, in the order of the flattened array; None for a scalar.
        description: The description given by the provider.
        label: The short axis or component label given by the provider.
        dtype: The value type of the field.
        units: The unit of the values, None if the values have no unit or it is unknown.
        fill: The fill value the provider uses for missing values, which the stored documents omit.
        valid_min: The smallest valid value given by the provider.
        valid_max: The largest valid value given by the provider.
        coordinate_system: The name of the coordinate system of a vector.
        vector_component: The meaning of this element within a vector.
        flag_meanings: The meaning of each value of a flag or status field.
        var_type: The ISTP variable type (VAR_TYPE), such as data or support_data.
        field_sources: For each field that has a value, the kind of source that set it.
    """

    name: str
    source_name: str
    component: str | int | None = None
    element_index: int | None = None
    description: str | None = None
    label: str | None = None
    dtype: DataType | None = None
    units: str | None = None
    fill: str | int | float | None = None
    valid_min: float | None = None
    valid_max: float | None = None
    coordinate_system: str | None = None
    vector_component: VectorComponent | None = None
    flag_meanings: dict[str, str] | None = None
    var_type: str | None = None
    field_sources: dict[str, MetadataSourceKind] = Field(default_factory=dict)

    @classmethod
    def from_single_source(cls, kind: MetadataSourceKind, **field_values: Any) -> "VariableMetadata":
        """Builds a variable whose values all come from one kind of source.

        Args:
            kind: The kind of source of every given value.
            **field_values: The field values; None means the source gives no value.

        Returns:
            The variable with field_sources set to the kind for each field that has a value.
        """
        variable = cls(**field_values)
        for field_name in cls.model_fields:
            if field_name != "field_sources" and getattr(variable, field_name) is not None:
                variable.field_sources[field_name] = kind
        return variable


class DatasetMetadata(BaseModel):
    """The technical metadata of one dataset, built from provider metadata only.

    Attributes:
        dataset_id: The dataset ID as named in the master catalog.
        collection: The MongoDB collection holding the data documents of the dataset.
        source: The system the dataset is ingested from.
        title: The title given by the provider.
        description: The description given by the provider, unchanged.
        time: The time axis of the dataset.
        citation: How the dataset is cited and under which terms it may be used.
        links: References to descriptions outside this project.
        variables: One entry per field stored in the data documents.
        not_stored: The provider parameters left out of the data documents.
        provenance: The sources the record was built from.
        updated_at: When the record was built.
    """

    dataset_id: str
    collection: str
    source: SourceInfo
    title: str | None = None
    description: str | None = None
    time: TimeInfo = Field(default_factory=TimeInfo)
    citation: CitationInfo = Field(default_factory=CitationInfo)
    links: LinkInfo = Field(default_factory=LinkInfo)
    variables: list[VariableMetadata] = Field(default_factory=list)
    not_stored: list[SkippedParameter] = Field(default_factory=list)
    provenance: list[MetadataSource] = Field(default_factory=list)
    updated_at: UtcDatetime = Field(default_factory=lambda: datetime.now(UTC))

    def replace_provenance(self, source: MetadataSource) -> None:
        """Replaces the provenance entry of the source kind, so a source applied again is listed once.

        Args:
            source: The source to list.
        """
        self.provenance = [entry for entry in self.provenance if entry.kind != source.kind]
        self.provenance.append(source)

    def to_document(self) -> dict[str, Any]:
        """Converts the record into a MongoDB document with the dataset ID as `_id`.

        Times stay datetime objects, so MongoDB stores them as dates.

        Returns:
            The document, built only from BSON types.
        """
        document = self.model_dump()
        document["_id"] = self.dataset_id
        return document

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "DatasetMetadata":
        """Builds a record from a document written by to_document.

        Args:
            document: The stored document.

        Returns:
            The record, with naive stored times read as UTC.

        Raises:
            pydantic.ValidationError: If the document does not match the schema.
        """
        fields = {key: value for key, value in document.items() if key != "_id"}
        return cls.model_validate(fields)


class RawMetadataRecord(BaseModel):
    """Provider metadata as received, kept beside the normalized record.

    Attributes:
        dataset_id: The dataset the metadata describes.
        kind: The kind of source the payload comes from.
        payload: The metadata as received, with values converted to BSON types.
        url: The location the payload was read from.
        retrieved_at: When the payload was read.
    """

    dataset_id: str
    kind: MetadataSourceKind
    payload: dict[str, Any]
    url: str | None = None
    retrieved_at: UtcDatetime
