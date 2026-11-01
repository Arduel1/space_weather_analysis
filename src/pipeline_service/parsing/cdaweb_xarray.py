import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from space_weather_analysis.metadata.models import MAX_ARRAY_COLUMNS, NotStoredReason, SkippedParameter

# ISTP marks the variables holding the measurements with VAR_TYPE "data"; support and metadata variables describe them.
DATA_VARIABLE_TYPE = "data"


@dataclass(frozen=True)
class VariablePlan:
    """Which data variables of a dataset are requested and which are known not to be stored.

    Attributes:
        requested: The data variables that fit into columns.
        not_stored: The data variables left out, with the reason.
    """

    requested: tuple[str, ...]
    not_stored: tuple[SkippedParameter, ...]


@dataclass
class DatasetFrame:
    """The columns built from one CDAS response and the attributes describing them.

    Attributes:
        frame: One column per 1-D variable and one per element `{variable}_{i}` of a small 2-D variable, indexed by time.
        attributes: The ISTP attributes of each stored variable and of each time axis, keyed by name.
        not_stored: The requested variables left out, with the reason.
    """

    frame: pd.DataFrame
    attributes: dict[str, dict[str, Any]] = field(default_factory=dict)
    not_stored: list[SkippedParameter] = field(default_factory=list)


def plan_master_variables(master: Mapping[str, Any]) -> VariablePlan:
    """Selects the data variables to request from the CDAWeb master file of a dataset.

    Only data variables with a value per record and at most MAX_ARRAY_COLUMNS elements are
    requested, so maps, images and spectra are neither downloaded nor stored.

    Args:
        master: The master file as parsed by the collector: `variables` with their `description` and `attributes`.

    Returns:
        The requested variables and the data variables known not to be stored.
    """
    master_variables = master.get("variables")
    requested: list[str] = []
    not_stored: list[SkippedParameter] = []
    for name, variable in (master_variables if isinstance(master_variables, dict) else {}).items():
        attributes = variable.get("attributes") or {}
        if str(attributes.get("VAR_TYPE", "")).strip() != DATA_VARIABLE_TYPE:
            continue
        description = variable.get("description") or {}
        dimension_sizes = description.get("DimSizes")
        element_count = math.prod(dimension_sizes) if isinstance(dimension_sizes, list) and dimension_sizes else 1
        if description.get("RecVariance") == "NOVARY":
            not_stored.append(SkippedParameter(name=name, reason=NotStoredReason.NOT_TIME_VARYING))
        elif element_count > MAX_ARRAY_COLUMNS:
            not_stored.append(SkippedParameter(name=name, reason=NotStoredReason.TOO_MANY_ELEMENTS))
        else:
            requested.append(name)
    return VariablePlan(requested=tuple(requested), not_stored=tuple(not_stored))


def dataset_to_frame(dataset: xr.Dataset, variables: Sequence[str], start: datetime, stop: datetime) -> DatasetFrame:
    """Builds time-indexed columns from the variables of a CDAS response, clipped to a time range.

    The time axis of a variable is its first dimension, which must be a datetime coordinate; the
    DEPEND_0 attribute is not used, as it does not always name the dimension the data lie on.
    Variables on different time axes are joined on their time stamps, so a row lacks the columns
    of the axes without a record at its time.

    Args:
        dataset: The data as delivered by cdasws.
        variables: The variables to convert; variables missing in the dataset are left out.
        start: The start of the range (inclusive), timezone-aware.
        stop: The end of the range (exclusive), timezone-aware.

    Returns:
        The columns indexed in UTC without time zone, as cdasws delivers the times, the attributes and the variables left out.
    """
    result = DatasetFrame(frame=pd.DataFrame())
    frames_by_axis: dict[str, list[pd.DataFrame]] = {}
    for name in variables:
        if name not in dataset.data_vars:
            continue
        variable = dataset[name]
        time_axis = str(variable.dims[0]) if variable.dims else None
        if time_axis is None or time_axis not in dataset.coords or not np.issubdtype(dataset[time_axis].dtype, np.datetime64):
            result.not_stored.append(SkippedParameter(name=name, reason=NotStoredReason.TIME_AXIS_NOT_RESOLVABLE))
            continue
        if variable.ndim > 2 or (variable.ndim == 2 and variable.shape[1] > MAX_ARRAY_COLUMNS):
            result.not_stored.append(SkippedParameter(name=name, reason=NotStoredReason.TOO_MANY_ELEMENTS))
            continue

        index = pd.DatetimeIndex(dataset[time_axis].to_numpy())
        values = variable.to_numpy()
        if variable.ndim == 1:
            frame = pd.DataFrame({name: values}, index=index)
        else:
            frame = pd.DataFrame(values, index=index, columns=[f"{name}_{element}" for element in range(values.shape[1])])
        # Nullable integers stay integers when the join with another time axis adds rows without value
        integer_columns = {column: "Int64" for column in frame.columns if pd.api.types.is_integer_dtype(frame[column])}
        frames_by_axis.setdefault(time_axis, []).append(frame.astype(integer_columns))
        result.attributes[name] = dict(variable.attrs)
        result.attributes.setdefault(time_axis, dict(dataset[time_axis].attrs))

    axis_frames = [_unique_times(pd.concat(frames, axis=1)) for frames in frames_by_axis.values()]
    if axis_frames:
        combined = pd.concat(axis_frames, axis=1, join="outer").sort_index()
        in_range = (combined.index >= _naive_utc(start)) & (combined.index < _naive_utc(stop))
        result.frame = combined[in_range]
    return result


def _unique_times(frame: pd.DataFrame) -> pd.DataFrame:
    """Keeps the first row of each time stamp, as one time axis yields one document per time.

    Args:
        frame: The columns of one time axis.

    Returns:
        The frame without repeated time stamps.
    """
    return frame[~frame.index.duplicated(keep="first")]


def _naive_utc(time: datetime) -> pd.Timestamp:
    """Converts an aware time into a UTC timestamp without time zone, the form of the cdasws time coordinates.

    Args:
        time: The aware time.

    Returns:
        The timestamp.
    """
    return pd.Timestamp(time.astimezone(UTC).replace(tzinfo=None))
