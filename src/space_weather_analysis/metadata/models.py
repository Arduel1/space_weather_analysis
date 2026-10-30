from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple, Union, get_args

from pydantic import BaseModel, Field, field_validator

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.util.time_utils import ensure_aware

# Where a metadata value comes from:
# hapi: the /info response of a HAPI server (Heliophysics Application Programmer's Interface, https://hapi-server.org/).
# istp: the attributes of a CDF file following the ISTP Metadata Guidelines (https://istp-metadata.readthedocs.io/).
# spase: a SPASE record (Space Physics Archive Search and Extract, https://spase-group.org/).
# curated: a value written in the code of this project.
# file: a value read from a downloaded data file without a metadata standard.
# api: the response of a provider API other than HAPI.
# cdas: the dataset list of CDAS, the CDAWeb Coordinated Data Analysis System web services (https://cdaweb.gsfc.nasa.gov/WebServices/REST/).
MetadataSourceKind = Literal["hapi", "istp", "spase", "curated", "file", "api", "cdas"]
METADATA_SOURCE_KINDS: Tuple[str, ...] = get_args(MetadataSourceKind)

# Value types of the stored fields, named like the HAPI parameter types.
DataType = Literal["double", "integer", "string", "isotime", "boolean"]

# Position of the time stamp in the measurement interval, the vocabulary of the HAPI 'timeStampLocation'.
TimestampLocation = Literal["begin", "center", "end", "other"]

# Meaning of a vector component, the vocabulary of the HAPI 3.1 'vectorComponents'.
VectorComponent = Literal["x", "y", "z", "r", "rho", "latitude", "colatitude", "longitude", "longitude0", "altitude", "other"]


class SourceInfo(BaseModel):
    """The system a dataset is ingested from.

    Attributes:
        type: The client family that ingests the dataset.
        provider: The organization providing the data, as named by the provider.
        server_url: The base URL of the server the data comes from.
    """

    type: SourceType
    provider: Optional[str] = None
    server_url: Optional[str] = None


class TimeInfo(BaseModel):
    """The time axis of a dataset.

    Attributes:
        coverage_start: The time of the first available record.
        coverage_stop: The time of the last available record.
        cadence: The time between records as ISO 8601 duration.
        timestamp_location: The position of each time stamp in its measurement interval.
    """

    coverage_start: Optional[datetime] = None
    coverage_stop: Optional[datetime] = None
    cadence: Optional[str] = None
    timestamp_location: Optional[TimestampLocation] = None

    @field_validator("coverage_start", "coverage_stop")
    @classmethod
    def _coverage_in_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        """Reads naive times as UTC, as MongoDB returns stored times without time zone."""
        return ensure_aware(value) if value is not None else None


class CitationInfo(BaseModel):
    """How a dataset is cited and under which terms it may be used.

    Attributes:
        doi: The Digital Object Identifier of the dataset, without resolver prefix.
        text: The citation text given by the provider.
        acknowledgement: The acknowledgement text requested by the provider.
        license: The license name or license URL.
        rules_of_use: The terms of use text or URL of the provider.
    """

    doi: Optional[str] = None
    text: Optional[str] = None
    acknowledgement: Optional[str] = None
    license: Optional[str] = None
    rules_of_use: Optional[str] = None


class LinkInfo(BaseModel):
    """References to descriptions of a dataset outside this project.

    Attributes:
        resource_url: The landing page of the dataset at the provider.
        spase_resource_id: The SPASE resource ID (Space Physics Archive Search and Extract, https://spase-group.org/).
        documentation: URLs of further documentation.
    """

    resource_url: Optional[str] = None
    spase_resource_id: Optional[str] = None
    documentation: List[str] = Field(default_factory=list)


class SkippedParameter(BaseModel):
    """A provider parameter that is not stored in the data documents.

    Attributes:
        name: The name of the parameter at the provider.
        reason: Why the parameter is not stored.
    """

    name: str
    reason: str


class MetadataSource(BaseModel):
    """One source the metadata record was built from.

    Attributes:
        kind: The kind of source.
        url: The location the metadata was read from.
        retrieved_at: When the metadata was read.
        last_modified: The modification time the source reports, as given by the source.
    """

    kind: MetadataSourceKind
    url: Optional[str] = None
    retrieved_at: datetime
    last_modified: Optional[str] = None

    @field_validator("retrieved_at")
    @classmethod
    def _retrieved_at_in_utc(cls, value: datetime) -> datetime:
        """Reads a naive time as UTC, as MongoDB returns stored times without time zone."""
        return ensure_aware(value)


class VariableMetadata(BaseModel):
    """The description of one field stored in the data documents.

    Attributes:
        name: The field name in the stored documents.
        source_name: The name of the variable at the provider.
        component: The label or index of the array element this field holds; the flat index for arrays with more than one dimension.
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
    component: Optional[Union[str, int]] = None
    description: Optional[str] = None
    label: Optional[str] = None
    dtype: Optional[DataType] = None
    units: Optional[str] = None
    fill: Optional[Union[str, int, float]] = None
    valid_min: Optional[float] = None
    valid_max: Optional[float] = None
    coordinate_system: Optional[str] = None
    vector_component: Optional[VectorComponent] = None
    flag_meanings: Optional[Dict[str, str]] = None
    var_type: Optional[str] = None
    field_sources: Dict[str, MetadataSourceKind] = Field(default_factory=dict)

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
    title: Optional[str] = None
    description: Optional[str] = None
    time: TimeInfo = Field(default_factory=TimeInfo)
    citation: CitationInfo = Field(default_factory=CitationInfo)
    links: LinkInfo = Field(default_factory=LinkInfo)
    variables: List[VariableMetadata] = Field(default_factory=list)
    not_stored: List[SkippedParameter] = Field(default_factory=list)
    provenance: List[MetadataSource] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("updated_at")
    @classmethod
    def _updated_at_in_utc(cls, value: datetime) -> datetime:
        """Reads a naive time as UTC, as MongoDB returns stored times without time zone."""
        return ensure_aware(value)

    def to_document(self) -> Dict[str, Any]:
        """Converts the record into a MongoDB document with the dataset ID as `_id`.

        Times stay datetime objects, so MongoDB stores them as dates.

        Returns:
            The document, built only from BSON types.
        """
        document = self.model_dump()
        document["_id"] = self.dataset_id
        return document

    @classmethod
    def from_document(cls, document: Dict[str, Any]) -> "DatasetMetadata":
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
    payload: Dict[str, Any]
    url: Optional[str] = None
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def _retrieved_at_in_utc(cls, value: datetime) -> datetime:
        """Reads a naive time as UTC, as MongoDB returns stored times without time zone."""
        return ensure_aware(value)
