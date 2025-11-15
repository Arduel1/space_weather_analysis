from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class VariableMetadata:
    """
    A standardized data structure for representing variable metadata.

    This class serves as a common format, ensuring that all pipelines
    convert their metadata into this structure before
    passing it to utilities like the MetadataWriter.
    """

    name: str
    description: Optional[str] = None
    units: Optional[str] = None
    fill: Optional[str] = None
    type: Optional[str] = None
    size: Optional[List[int]] = None

    # Specific to metadata found only in downloaded files (e.g., CDF attributes)
    file_attributes: Dict[str, Any] = field(default_factory=dict)
