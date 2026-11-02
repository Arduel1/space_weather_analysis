from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class TaskIntervalSettings(BaseSettings):
    """Intervals of the metadata worker's task kinds, read from environment variables `METADATA_WORKER_{field name}` in upper case.

    Attributes:
        hapi_mirror_interval_hours: The time between two reads of each HAPI mirror file.
        cdas_interval_hours: The time between two reads of the CDAS dataset list.
        cdaweb_master_interval_hours: The time between two reads of the master file of one CDAWeb dataset.
        spase_interval_hours: The time between two reads of the SPASE record of one CDAWeb dataset.
    """

    model_config = SettingsConfigDict(env_prefix="METADATA_WORKER_", frozen=True)

    hapi_mirror_interval_hours: float = 24.0
    cdas_interval_hours: float = 24.0
    cdaweb_master_interval_hours: float = 168.0
    spase_interval_hours: float = 168.0


class CatalogSettings(BaseSettings):
    """Inputs of the metadata worker's catalog build, read from environment variables `METADATA_WORKER_{field name}` in upper case.

    Attributes:
        hapi_server_list_file: The list of HAPI server base URLs, one per line, whose mirrored datasets the catalog holds;
            a relative path starts at the working directory.
    """

    model_config = SettingsConfigDict(env_prefix="METADATA_WORKER_", frozen=True)

    hapi_server_list_file: Path = Path("data/hapi_config/all.txt")
