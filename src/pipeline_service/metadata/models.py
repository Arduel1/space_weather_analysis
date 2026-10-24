from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass(slots=True)
class VariableMetadata:
    """
    A standardized data structure for representing variable metadata.

    This class serves as a common format, ensuring that pipelines
    convert their metadata into this structure before
    passing it to the MetadataWriter.

    Attributes:
        name: The name of the variable.
        description: A human-readable description of the variable.
        units: The physical units of the variable as reported by the source.
        fill: The fill value used to denote missing data. Can be a string or number.
        type: The data type name of the variable as reported by the source.
        size: The array dimensions of the variable as a list of lengths, or None if the source reports none.
        file_attributes: Raw metadata/attributes extracted directly from the source file.
    """

    name: str
    description: Optional[str] = None
    units: Optional[str] = None
    fill: Optional[Union[str, int, float]] = None
    type: Optional[str] = None
    size: Optional[List[int]] = None

    # Attributes parsed from the downloaded data file rather than from the source API
    file_attributes: Dict[str, Any] = field(default_factory=dict)
