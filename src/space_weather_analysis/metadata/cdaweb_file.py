from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from space_weather_analysis.catalog.models import SourceType
from space_weather_analysis.metadata.istp_attributes import (
    istp_numeric_attribute,
    istp_scalar_attribute,
    istp_text_attribute,
    map_columns_to_istp_variables,
    time_stamp_location,
)
from space_weather_analysis.metadata.models import (
    DatasetMetadata,
    DataType,
    MetadataSource,
    MetadataSourceKind,
    SkippedParameter,
    SourceInfo,
    TimeInfo,
    VariableMetadata,
)
from space_weather_analysis.metadata.normalize import normalize_units


@dataclass
class DeliveredMetadata:
    """The metadata delivered with the chunks of one dataset in a run, collected for one record per dataset.

    Attributes:
        variable_attributes: The ISTP attributes of each delivered variable and time axis, keyed by name.
        column_types: The value type of each stored column.
        not_stored: The variables left out of the documents, keyed by name.
    """

    variable_attributes: dict[str, Any] = field(default_factory=dict)
    column_types: dict[str, DataType | None] = field(default_factory=dict)
    not_stored: dict[str, SkippedParameter] = field(default_factory=dict)


def build_cdaweb_file_metadata(dataset_id: str, collection: str, delivered: DeliveredMetadata, retrieved_at: datetime) -> DatasetMetadata:
    """Builds the metadata record of a CDAWeb dataset from the ISTP attributes delivered with its data files.

    ISTP (International Solar-Terrestrial Physics Metadata Guidelines, https://istp-metadata.readthedocs.io/) defines
    the attributes of each variable, such as unit (UNITS), description (CATDESC) and valid range (VALIDMIN/VALIDMAX).

    Args:
        dataset_id: The dataset ID.
        collection: The MongoDB collection holding the data documents.
        delivered: The metadata delivered by the chunks of the run.
        retrieved_at: When the data files were read.

    Returns:
        The metadata record; every set variable field is attributed to the source kind "file".
    """
    return DatasetMetadata(
        dataset_id=dataset_id,
        collection=collection,
        source=SourceInfo(type=SourceType.CDAWEB),
        time=TimeInfo(timestamp_location=time_stamp_location(delivered.variable_attributes)),
        variables=_istp_variables(delivered.variable_attributes, delivered.column_types),
        not_stored=list(delivered.not_stored.values()),
        provenance=[MetadataSource(kind=MetadataSourceKind.FILE, retrieved_at=retrieved_at)],
        updated_at=retrieved_at,
    )


def column_data_type(column: pd.Series) -> DataType | None:
    """Names the value type of a stored column.

    Args:
        column: The column as stored, after cleaning.

    Returns:
        'boolean', 'integer', 'double' or 'string', or None for any other pandas type.
    """
    if pd.api.types.is_bool_dtype(column):
        return DataType.BOOLEAN
    if pd.api.types.is_integer_dtype(column):
        return DataType.INTEGER
    if pd.api.types.is_float_dtype(column):
        return DataType.DOUBLE
    if pd.api.types.is_string_dtype(column) or pd.api.types.is_object_dtype(column):
        return DataType.STRING
    return None


def _istp_variables(variable_attributes: dict[str, Any], stored_column_types: dict[str, DataType | None]) -> list[VariableMetadata]:
    """Describes each stored column by the ISTP attributes of its variable as delivered with the data.

    Args:
        variable_attributes: The attributes of each delivered variable, keyed by its name.
        stored_column_types: The value type of each stored column.

    Returns:
        One variable per stored column; a column without variable carries only its name and type.
    """
    column_sources = map_columns_to_istp_variables(stored_column_types, variable_attributes)
    variables: list[VariableMetadata] = []
    for column_name, data_type in stored_column_types.items():
        variable_name, component_index = column_sources.get(column_name, (column_name, None))
        attributes = variable_attributes.get(variable_name)
        if not isinstance(attributes, dict):
            attributes = {}
        variables.append(
            VariableMetadata.from_single_source(
                MetadataSourceKind.FILE,
                name=column_name,
                source_name=variable_name,
                component=component_index,
                element_index=component_index,
                # FIELDNAM is the short name every ISTP variable carries, used when CATDESC is missing
                description=istp_text_attribute(attributes, "CATDESC") or istp_text_attribute(attributes, "FIELDNAM"),
                label=istp_text_attribute(attributes, "LABLAXIS"),
                dtype=data_type,
                units=normalize_units(istp_text_attribute(attributes, "UNITS")),
                fill=istp_scalar_attribute(attributes.get("FILLVAL")),
                valid_min=istp_numeric_attribute(attributes.get("VALIDMIN")),
                valid_max=istp_numeric_attribute(attributes.get("VALIDMAX")),
                var_type=istp_text_attribute(attributes, "VAR_TYPE"),
            )
        )
    return variables
