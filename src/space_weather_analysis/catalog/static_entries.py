import pathlib
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from space_weather_analysis.catalog.models import CatalogEntryDict, MasterCatalogItem, SourceType
from space_weather_analysis.util.config_files import CLOSED, load_toml
from space_weather_analysis.util.time_utils import parse_iso_duration, parse_iso_time


class StaticTimeRange(BaseModel):
    """The time range of a configured source, as ISO 8601 times.

    Attributes:
        start_time: The first time the source holds data for.
        end_time: The last time the source holds data for; an ongoing source is asked beyond it.
    """

    model_config = CLOSED

    start_time: str
    end_time: str

    @field_validator("start_time", "end_time")
    @classmethod
    def _parsable_time(cls, value: str) -> str:
        """Rejects a time that is no ISO 8601 time."""
        if parse_iso_time(value) is None:
            raise ValueError(f"'{value}' is no ISO 8601 time.")
        return value


class StaticCatalogSource(BaseModel):
    """A configured source of a dataset, stored as one source of its master catalog item.

    Attributes:
        type: The source type.
        source_id: The identifier of the source within its type, the server URL for HAPI.
        time_range: The time range the source holds data for.
        ongoing: Whether the source still receives new data.
        cadence: The time between two records as ISO 8601 duration, which sizes the fetched chunks while the
            dataset has no metadata record giving it.
    """

    model_config = CLOSED

    type: SourceType
    source_id: str = Field(min_length=1)
    time_range: StaticTimeRange
    ongoing: bool = False
    cadence: str | None = None

    @field_validator("cadence")
    @classmethod
    def _parsable_cadence(cls, value: str | None) -> str | None:
        """Rejects a cadence that is no ISO 8601 duration."""
        if value is not None and parse_iso_duration(value) is None:
            raise ValueError(f"'{value}' is no ISO 8601 duration.")
        return value

    def to_catalog_entry(self) -> CatalogEntryDict:
        """Builds the catalog source of this configured source.

        Returns:
            The catalog source, holding the cadence only if one is configured.
        """
        entry: CatalogEntryDict = {
            "type": self.type.value,
            "source_id": self.source_id,
            "time_range": {"start_time": self.time_range.start_time, "end_time": self.time_range.end_time},
            "ongoing": self.ongoing,
        }
        if self.cadence is not None:
            entry["cadence"] = self.cadence
        return entry


class StaticCatalogEntry(BaseModel):
    """A configured master catalog item: a dataset with its sources in the order they are asked.

    Attributes:
        dataset_id: The dataset ID.
        sources: The sources of the dataset.
    """

    model_config = CLOSED

    dataset_id: str = Field(min_length=1)
    sources: list[StaticCatalogSource] = Field(min_length=1)

    def to_catalog_item(self) -> MasterCatalogItem:
        """Builds the master catalog item of this entry.

        Returns:
            The catalog item.
        """
        return {"dataset_id": self.dataset_id, "sources": [source.to_catalog_entry() for source in self.sources]}


class StaticCatalogFile(BaseModel):
    """The content of the static catalog file, a TOML array of tables named `dataset`.

    Attributes:
        dataset: The configured catalog items in the order of the file.
    """

    model_config = CLOSED

    dataset: list[StaticCatalogEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_dataset_ids(self) -> Self:
        """Rejects a file that lists a dataset twice, as the catalog holds one item per dataset."""
        dataset_ids = [entry.dataset_id for entry in self.dataset]
        duplicates = sorted({dataset_id for dataset_id in dataset_ids if dataset_ids.count(dataset_id) > 1})
        if duplicates:
            raise ValueError(f"Dataset IDs must be unique, repeated: {duplicates}")
        return self


def load_static_catalog(path: pathlib.Path) -> list[MasterCatalogItem]:
    """Reads the master catalog items of a static catalog file.

    Args:
        path: The path of the TOML file.

    Returns:
        The catalog items in the order of the file.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is no valid TOML.
        pydantic.ValidationError: If the content does not describe valid catalog items.
    """
    catalog_file = load_toml(path, StaticCatalogFile)
    return [entry.to_catalog_item() for entry in catalog_file.dataset]
