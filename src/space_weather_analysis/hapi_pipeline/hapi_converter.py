import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import sunpy.timeseries
from hapiclient.hapitime import hapitime2datetime

logger = logging.getLogger(__name__)


def convert_hapi_to_timeseries(data: np.ndarray, meta: Dict[str, Any]) -> Optional[sunpy.timeseries.TimeSeries]:
    """
    Converts a HAPI NumPy array and metadata into a sunpy.timeseries.TimeSeries.

    Args:
        data (np.ndarray): The NumPy structured array returned by the
                           hapiclient.
        meta (Dict[str, Any]): The metadata dictionary returned by the
                               hapiclient.

    Returns:
        Optional[sunpy.timeseries.TimeSeries]: A TimeSeries object if
                                               conversion is successful,
                                               otherwise None.
    """
    if data is None or meta is None or len(data) == 0:
        logger.warning("Cannot convert empty data to TimeSeries.")
        return None

    try:
        # Convert time
        time_values = hapitime2datetime(data["Time"])
        df = pd.DataFrame(index=pd.Index(time_values, name="Time"))

        # Process parameters
        for param in meta.get("parameters", []):
            param_name = param.get("name")
            if param_name == "Time" or param_name not in data.dtype.names:
                continue

            param_data = data[param_name]
            param_size = param.get("size")

            if param_size:
                # Array parameter (e.g., vector) -> split into columns
                if len(param_data.shape) == 2 and param_data.shape[1] == param_size[0]:
                    labels = param.get("label")
                    if isinstance(labels, list) and len(labels) == param_size[0]:
                        col_names = [f"{param_name}_{l}" for l in labels]
                    else:
                        col_names = [f"{param_name}_{i}" for i in range(param_size[0])]

                    df_param = pd.DataFrame(param_data, index=df.index, columns=col_names)
                    df = pd.concat([df, df_param], axis=1)
            else:
                # Scalar parameter
                df[param_name] = param_data

        # Convert to TimeSeries object
        return sunpy.timeseries.TimeSeries(df, meta)

    except Exception as e:
        logger.error(f"Failed to convert HAPI data to TimeSeries: {e}", exc_info=True)
        return None
