import pathlib
import tomllib
from datetime import date
from functools import cache
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from space_weather_analysis.metadata.models import TimestampLocation

# The model of every table is closed, so a mistyped key fails the validation instead of being ignored
_CLOSED = ConfigDict(extra="forbid", frozen=True)


class CuratedSettings(BaseSettings):
    """Settings of the curated metadata, read from environment variables `METADATA_{field name}` in upper case.

    Attributes:
        curated_dir: The directory holding one TOML file of curated metadata per provider.
    """

    model_config = SettingsConfigDict(env_prefix="METADATA_", frozen=True)

    curated_dir: pathlib.Path = pathlib.Path("config/metadata")


class CuratedReference(BaseModel):
    """A page the curated values of a dataset were taken from, so that they can be checked and updated.

    Attributes:
        url: The address of the page.
        retrieved: The day the page was read.
        covers: Which values come from the page.
    """

    model_config = _CLOSED

    url: str = Field(min_length=1)
    retrieved: date
    covers: str = Field(min_length=1)


class CuratedCitation(BaseModel):
    """How a dataset is cited and under which terms it may be used, as curated.

    Attributes:
        doi: The Digital Object Identifier of the dataset, without resolver prefix.
        text: The citation text.
        acknowledgement: The acknowledgement text the provider requests, verbatim.
        license: The license name or license URL.
        rules_of_use: The terms of use text or URL.
    """

    model_config = _CLOSED

    doi: str | None = None
    text: str | None = None
    acknowledgement: str | None = None
    license: str | None = None
    rules_of_use: str | None = None


class CuratedTime(BaseModel):
    """The time axis of a dataset, as curated.

    Attributes:
        cadence: The time between records as ISO 8601 duration.
        timestamp_location: The position of each time stamp in its measurement interval.
    """

    model_config = _CLOSED

    cadence: str | None = None
    timestamp_location: TimestampLocation | None = None


class CuratedVariable(BaseModel):
    """The curated description of one stored field.

    Attributes:
        name: The field name in the data documents.
        label: The short label.
        description: What the field holds.
        units: The unit of the values.
        fill: The value the provider writes for a missing measurement.
        valid_min: The smallest valid value.
        valid_max: The largest valid value.
    """

    model_config = _CLOSED

    name: str = Field(min_length=1)
    label: str | None = None
    description: str | None = None
    units: str | None = None
    fill: str | float | None = None
    valid_min: float | None = None
    valid_max: float | None = None


class CuratedDataset(BaseModel):
    """The curated metadata of one dataset; every value needs a reference it was taken from.

    Attributes:
        title: The title of the dataset.
        description: The description of the dataset.
        provider: The organization providing the data.
        resource_url: The landing page of the dataset at the provider.
        citation: How the dataset is cited and under which terms it may be used.
        time: The time axis.
        variable: The curated fields.
        reference: The pages the values were taken from, at least one.
    """

    model_config = _CLOSED

    title: str | None = None
    description: str | None = None
    provider: str | None = None
    resource_url: str | None = None
    citation: CuratedCitation = Field(default_factory=CuratedCitation)
    time: CuratedTime = Field(default_factory=CuratedTime)
    variable: list[CuratedVariable] = Field(default_factory=list)
    reference: list[CuratedReference] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_variables(self) -> Self:
        """Rejects a dataset that describes one field twice."""
        names = [variable.name for variable in self.variable]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"Fields described more than once: {repeated}")
        return self


class _CuratedFile(BaseModel):
    """The content of one curated metadata file, a table `dataset` with one subtable per dataset ID."""

    model_config = _CLOSED

    dataset: dict[str, CuratedDataset] = Field(default_factory=dict)


def load_curated(directory: pathlib.Path) -> dict[str, CuratedDataset]:
    """Reads every TOML file of a directory into the curated metadata by dataset ID.

    Args:
        directory: The directory holding the curated metadata files; a missing directory holds none.

    Returns:
        The curated metadata by dataset ID.

    Raises:
        OSError: If a file cannot be read.
        tomllib.TOMLDecodeError: If a file is no valid TOML.
        pydantic.ValidationError: If a file does not describe valid curated metadata.
        ValueError: If two files describe the same dataset.
    """
    curated: dict[str, CuratedDataset] = {}
    for path in sorted(directory.glob("*.toml")):
        with path.open("rb") as file:
            datasets = _CuratedFile.model_validate(tomllib.load(file)).dataset
        repeated = sorted(curated.keys() & datasets.keys())
        if repeated:
            raise ValueError(f"{path.name} describes datasets that another curated file describes too: {repeated}")
        curated.update(datasets)
    return curated


@cache
def configured_curated() -> dict[str, CuratedDataset]:
    """Reads the curated metadata of the configured directory once per process; a changed file takes effect with the next start.

    Returns:
        The curated metadata by dataset ID.

    Raises:
        OSError, tomllib.TOMLDecodeError, pydantic.ValidationError, ValueError: As load_curated.
    """
    return load_curated(CuratedSettings().curated_dir)
