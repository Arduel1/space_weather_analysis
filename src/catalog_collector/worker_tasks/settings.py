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
