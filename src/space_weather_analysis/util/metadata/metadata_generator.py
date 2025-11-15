import logging
import pathlib
from typing import Dict, List, Optional

from space_weather_analysis.util.metadata.variable_metadata import VariableMetadata

logger = logging.getLogger(__name__)


class MetadataWriter:
    """
    Creates Markdown metadata files from standardized VariableMetadata objects.
    """

    def write_metadata_file(
        self,
        dataset_id: str,
        output_dir: pathlib.Path,
        variables: List[VariableMetadata],
        source_info: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Combines metadata and writes it to a markdown document.

        Args:
            dataset_id (str): The ID of the dataset.
            output_dir (pathlib.Path): The output directory.
            variables (List[VariableMetadata]): A list of standardized
                                                variable metadata objects.
            source_info (Optional[Dict[str, str]]): Optional header info,
                                                   such as source server or citation.
        """
        logger.info(f"Generating metadata document for {dataset_id}...")

        if not variables:
            logger.warning("No variable metadata provided. Skipping document generation.")
            return

        output_path = output_dir / f"{dataset_id}_metadata.md"

        if output_path.exists():
            logger.info(f"Metadata document {output_path} already exists. Skipping.")
            return

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"# Metadata for Dataset: {dataset_id}\n\n")

                if source_info:
                    for key, value in source_info.items():
                        f.write(f"**{key}:** {value}\n")
                    f.write("\n")

                f.write(
                    "This document combines metadata from the data source API "
                    "(e.g., HAPI) and, if available, attributes from the "
                    "downloaded data files (CDF/NC).\n\n"
                )
                f.write("---\n\n## Variables\n\n")

                for var in sorted(variables, key=lambda v: v.name):
                    f.write(f"### Variable: `{var.name}`\n\n")

                    # API/HAPI metadata
                    f.write("**Source Attributes (API/HAPI):**\n\n")
                    f.write(f"- **Description:** {var.description or 'N/A'}\n")
                    f.write(f"- **Type:** {var.type or 'N/A'}\n")
                    f.write(f"- **Units:** {var.units or 'N/A'}\n")
                    f.write(f"- **Fill Value:** `{var.fill or 'N/A'}`\n")
                    if var.size:
                        f.write(f"- **Size:** {var.size}\n")
                    f.write("\n")

                    # File metadata (if present)
                    if var.file_attributes:
                        f.write("**File Attributes (CDF/NC):**\n\n")
                        f.write("| Attribute | Value |\n")
                        f.write("|---|---|\n")
                        for key, value in var.file_attributes.items():
                            if isinstance(value, bytes):
                                value = value.decode("utf-8", errors="ignore")
                            value_str = str(value).replace("\n", " ")
                            f.write(f"| `{key}` | `{value_str}` |\n")
                        f.write("\n")

            logger.info(f"Successfully generated metadata: {output_path}")

        except Exception as e:
            logger.error(f"Failed to write metadata file: {e}", exc_info=True)
