import logging
import pathlib
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def generate_metadata_file(
    target_dataset: str,
    api_metadata: List[Dict[str, Any]],
    file_metadata: Dict[str, Any],
    output_dir: pathlib.Path,
):
    """
    Combines API and File metadata into a markdown document
    in the dataset's download directory.

    Args:
        target_dataset (str): The dataset ID.
        api_metadata (List[Dict]): Result from CdawebClient (API query).
        file_metadata (Dict): Result from file_parser (file parsing).
        output_dir (pathlib.Path): The specific download directory
                                   (e.g., .../GOLD/GOLD_L2_ON2).
    """
    logger.info(f"Generating metadata document for {target_dataset}...")

    if not api_metadata and not file_metadata:
        logger.warning("No API or File metadata provided. Skipping document generation.")
        return

    output_path = output_dir / f"{target_dataset}_metadata.md"

    if output_path.exists():
        logger.info(f"Metadata document {output_path} already exists. Skipping.")
        return

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# Metadata for Dataset: {target_dataset}\n\n")
            f.write(
                "This document combines metadata from the CDAWeb API "
                "and attributes from the downloaded data file (CDF/NC).\n\n"
            )

            # Use 'Name' from API, as normalized by CdawebClient
            api_meta_dict = {var["Name"]: var for var in api_metadata if var.get("Name")} if api_metadata else {}
            all_var_names = set(api_meta_dict.keys()) | set(file_metadata.keys())

            if not all_var_names:
                f.write("No variable metadata could be found from API or files.\n")

            for var_name in sorted(list(all_var_names)):
                f.write(f"## Variable: `{var_name}`\n\n")

                # 1. Get description from API
                api_attrs = api_meta_dict.get(var_name)
                # Use 'LongDescription' as provided by CdawebClient
                if api_attrs and api_attrs.get("LongDescription"):
                    f.write(f"**Description (API):** {api_attrs.get('LongDescription')}\n\n")
                else:
                    f.write("**Description (API):** *N/A*\n\n")

                # 2. Get attributes from File
                f.write("**File Attributes (CDF/NC):**\n\n")
                if var_name in file_metadata and file_metadata[var_name]:
                    file_attrs = file_metadata[var_name]
                    f.write("| Attribute | Value |\n")
                    f.write("|---|---|\n")
                    for key, value in file_attrs.items():
                        if isinstance(value, bytes):
                            value = value.decode("utf-8", errors="ignore")
                        # Sanitize newlines for markdown table
                        value_str = str(value).replace("\n", " ")
                        f.write(f"| `{key}` | `{value_str}` |\n")
                    f.write("\n")
                else:
                    f.write(f"*No file attributes found for '{var_name}'.*\n\n")

        logger.info(f"Successfully generated metadata: {output_path}")

    except Exception as e:
        logger.error(f"Failed to write metadata file: {e}", exc_info=True)
