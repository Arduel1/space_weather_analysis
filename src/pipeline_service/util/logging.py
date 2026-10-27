import logging
import pathlib
from typing import Optional

import xarray as xr

logger = logging.getLogger(__name__)


def log_xarray_dataset_summary(ds: xr.Dataset, file_path_obj: Optional[pathlib.Path] = None) -> None:
    """Logs a summary of an XArray Dataset for diagnostic purposes.

    Args:
        ds: The dataset to inspect.
        file_path_obj: Optional path to the source file for context in logs.
    """
    source_name = file_path_obj.name if file_path_obj else "InMemoryDataset"

    logger.info(f"--- XArray Dataset Summary: {source_name} ---")
    logger.info(f"Dimensions: {dict(ds.sizes)}")

    coords = list(ds.coords)
    logger.info(f"Coordinates ({len(coords)}): {coords}")

    data_vars = list(ds.data_vars)
    logger.info(f"Data Variables ({len(data_vars)}): {data_vars}")

    # Log attrs keys only to avoid flooding logs with large metadata
    attrs_keys = list(ds.attrs.keys())
    logger.info(f"Global Attributes ({len(attrs_keys)}): {attrs_keys}")
    logger.info("---------------------------------------------")
