from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass(slots=True)
class VariableMetadata:
    """
    A standardized data structure for representing variable metadata.

    This class serves as a common format, ensuring that all pipelines
    convert their metadata into this structure before
    passing it to utilities like the MetadataWriter.

    Attributes:
        name: The name of the variable.
        description: A human-readable description of the variable.
        units: The physical units of the variable (e.g., 'nT', 'km/s').
        fill: The fill value used to denote missing data. Can be a string or number.
        type: The data type of the variable (e.g., 'float', 'int').
        size: The shape/dimensions of the variable if it is an array (e.g., [3]).
        file_attributes: Raw metadata/attributes extracted directly from the source file.
    """

    name: str
    description: Optional[str] = None
    units: Optional[str] = None
    fill: Optional[Union[str, int, float]] = None
    type: Optional[str] = None
    size: Optional[List[int]] = None

    # Specific to metadata found only in downloaded files (e.g., CDF attributes)
    file_attributes: Dict[str, Any] = field(default_factory=dict)
