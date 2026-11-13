import pathlib
import re
from collections.abc import Mapping
from typing import Self, cast

import pandas as pd
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from space_weather_analysis.util.config_files import CLOSED, load_toml
from space_weather_analysis.util.time_utils import IsoDuration


class SwpcSettings(BaseSettings):
    """Settings of the source of the NOAA Space Weather Prediction Center (SWPC, https://www.swpc.noaa.gov/).

    Read from environment variables `SWPC_{field name}` in upper case.

    Attributes:
        products_file: The TOML file describing the SWPC products, one table per dataset.
    """

    model_config = SettingsConfigDict(env_prefix="SWPC_", frozen=True)

    products_file: pathlib.Path = pathlib.Path("config/sources/swpc.toml")


class RollingFile(BaseModel):
    """One rolling file of a product, which SWPC replaces with the values of the latest span.

    Attributes:
        length: The span the file holds, ending now.
        path: The path of the file below the base URL of SWPC.
    """

    model_config = CLOSED

    length: IsoDuration
    path: str = Field(min_length=1)


class ChannelSplit(BaseModel):
    """How rows that each hold one channel of a time become one row per time with one field per channel.

    The channel of a row is named either through `names` or through `pattern` and `template`.

    Attributes:
        field: The field naming the channel of a row.
        names: The field name prefix of each channel value; rows of other channels are left out.
        pattern: A regular expression with one group, matched against the channel value; rows not matching are left out.
        template: The field name prefix built from the group of the pattern, which takes its place at `{}`.
        shared: Fields equal for all channels of a time, stored once per time from its first row.
    """

    model_config = CLOSED

    field: str = Field(min_length=1)
    names: dict[str, str] = Field(default_factory=dict)
    pattern: str | None = None
    template: str | None = None
    shared: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_naming(self) -> Self:
        """Rejects a split with both or neither naming, a pattern without one group and a template without one `{}`."""
        if bool(self.names) == (self.pattern is not None):
            raise ValueError("Give either names or a pattern with template for the channels, not both.")
        if self.pattern is not None:
            if self.template is None or self.template.count("{}") != 1:
                raise ValueError("A channel pattern needs a template with exactly one `{}`.")
            if re.compile(self.pattern).groups != 1:
                raise ValueError(f"The channel pattern '{self.pattern}' must have exactly one group.")
        return self

    def prefixes(self, channels: pd.Series) -> pd.Series:
        """Maps the channel value of each row to its field name prefix; NaN for rows of unknown channels."""
        if self.names:
            return channels.map(self.names)
        # The text of the template around `{}` encloses the group the pattern captures from each channel value
        before, after = cast("str", self.template).split("{}")
        return before + channels.astype(str).str.extract(cast("str", self.pattern), expand=False) + after


class SwpcProduct(BaseModel):
    """A data product SWPC publishes as rolling JSON files of rows; its descriptions are curated in the metadata files.

    Attributes:
        provider_id: The product name in the source identifier of the documents.
        files: The rolling files of the product by ascending length.
        time_field: The field holding the UTC time of a row; a time without zone designator counts as UTC.
        fields: The value fields to store, mapped to their stored name, or with a channel split to the suffix after the channel prefix.
        filter: Values that the fields of a row must equal for the row to be kept.
        channel: How rows of one channel each are joined into one row per time; None stores each row as it is.
        records_per_time: Whether several rows of one time are separate records, such as the sunspot regions of one
            day, which are all kept; otherwise a file listing a time twice keeps its first row.
        reaches_ahead: Whether the file is one run of a model that also holds values after now, such as the solar wind
            WSA-Enlil computes for the coming days; every run stores the whole file, replacing the previous run.
    """

    model_config = CLOSED

    provider_id: str = Field(min_length=1)
    files: list[RollingFile] = Field(min_length=1)
    time_field: str = "time_tag"
    fields: dict[str, str] = Field(min_length=1)
    filter: dict[str, str | int | float | bool] = Field(default_factory=dict)
    channel: ChannelSplit | None = None
    records_per_time: bool = False
    reaches_ahead: bool = False

    @model_validator(mode="after")
    def _files_by_length(self) -> Self:
        """Rejects files that are not sorted by ascending length, as the shortest covering file is chosen first."""
        lengths = [file.length for file in self.files]
        if lengths != sorted(lengths):
            raise ValueError(f"The files of product '{self.provider_id}' must be sorted by ascending length.")
        return self

    def parse(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Converts the rows of a file into one row per time, or one row per record for records_per_time.

        Args:
            rows: The rows of the file, one column per field.

        Returns:
            The stored fields with a sorted UTC DatetimeIndex without name; rows without valid time are left out.

        Raises:
            KeyError: If the rows lack a field the product names.
        """
        for field, value in self.filter.items():
            rows = rows[rows[field] == value]
        times = pd.to_datetime(rows[self.time_field], utc=True, errors="coerce")
        if self.channel is not None:
            return _channels_to_columns(rows.assign(time=times), self.channel, self.fields)
        frame = rows.assign(time=times).dropna(subset=["time"]).set_index("time")[list(self.fields)].rename(columns=self.fields)
        if not self.records_per_time:
            # A file listing a time twice keeps its first row
            frame = frame[~frame.index.duplicated()]
        frame.index.name = None
        return frame.sort_index()


def _channels_to_columns(rows: pd.DataFrame, channel: ChannelSplit, values: Mapping[str, str]) -> pd.DataFrame:
    """Turns rows with one channel each into one row per time with one column per channel and value.

    Args:
        rows: The rows of the file with their parsed UTC time in the column `time`.
        channel: How the channel of each row is named.
        values: The value columns of the rows to keep, mapped to the field name suffix.

    Returns:
        One row per time, sorted, with the columns `{channel prefix}_{suffix}` and the shared fields of the first row of each time.
    """
    named = rows.assign(channel=channel.prefixes(rows[channel.field])).dropna(subset=["time", "channel"])
    # One row per time and one column per pair of value column and channel, from the first row of each pair
    wide = named.pivot_table(index="time", columns="channel", values=list(values), aggfunc="first")
    # The pivot names each column by the pair of value column and channel, which the pandas stubs type as plain labels
    column_pairs = cast("list[tuple[str, str]]", wide.columns.tolist())
    wide.columns = [f"{prefix}_{values[value]}" for value, prefix in column_pairs]
    for field in channel.shared:
        wide[field] = named.groupby("time")[field].first()
    wide.index.name = None
    return wide.sort_index()


class _ProductsFile(BaseModel):
    """The content of the SWPC products file: one table per dataset under `product`."""

    model_config = CLOSED

    product: dict[str, SwpcProduct] = Field(default_factory=dict)


def load_swpc_products(path: pathlib.Path) -> dict[str, SwpcProduct]:
    """Reads the SWPC products file.

    Args:
        path: The path of the TOML file.

    Returns:
        The products by dataset ID.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is no valid TOML.
        pydantic.ValidationError: If a product is invalid.
    """
    return load_toml(path, _ProductsFile).product


def configured_swpc_products() -> dict[str, SwpcProduct]:
    """Reads the SWPC products file of the settings.

    Returns:
        The products by dataset ID.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is no valid TOML.
        pydantic.ValidationError: If a product or a setting is invalid.
    """
    return load_swpc_products(SwpcSettings().products_file)
