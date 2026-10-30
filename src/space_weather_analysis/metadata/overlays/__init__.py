from collections.abc import Sequence

from space_weather_analysis.metadata.models import DatasetMetadata
from space_weather_analysis.metadata.overlays.cdas import apply_cdas_overlay
from space_weather_analysis.metadata.overlays.cdaweb import cdas_dataset_id, describes_cdaweb_dataset
from space_weather_analysis.metadata.overlays.istp import apply_istp_overlay
from space_weather_analysis.metadata.repository import MetadataRepository


def save_with_overlays(metadata_repository: MetadataRepository, records: Sequence[DatasetMetadata]) -> None:
    """Saves records after laying the stored metadata of further sources over them.

    Every writer of normalized records saves through this function, so a record rebuilt from one
    source keeps the fields another source is authoritative for. CDAWeb records get the master file
    (raw kind "istp") first and the CDAS entry (raw kind "cdas") last, as CDAS ranks highest for its dataset fields.

    Args:
        metadata_repository: The repository holding records and raw metadata.
        records: The records to save, changed in place by the overlays.
    """
    cdaweb_records = [record for record in records if describes_cdaweb_dataset(record)]
    if cdaweb_records:
        cdas_ids = sorted({cdas_dataset_id(record.dataset_id) for record in cdaweb_records})
        raw_masters = metadata_repository.get_raw_many(cdas_ids, "istp")
        raw_cdas_entries = metadata_repository.get_raw_many(cdas_ids, "cdas")
        for record in cdaweb_records:
            cdas_id = cdas_dataset_id(record.dataset_id)
            raw_master = raw_masters.get(cdas_id)
            if raw_master is not None:
                apply_istp_overlay(record, raw_master.payload, raw_master.retrieved_at, raw_master.url)
            raw_cdas_entry = raw_cdas_entries.get(cdas_id)
            if raw_cdas_entry is not None:
                apply_cdas_overlay(record, raw_cdas_entry.payload, raw_cdas_entry.retrieved_at, raw_cdas_entry.url)
    metadata_repository.save_many(records)


def resave_cdaweb_records(metadata_repository: MetadataRepository, dataset_ids: Sequence[str]) -> int:
    """Saves the stored CDAWeb records of the datasets again, so changed raw metadata of a further source reaches them.

    Records of another source, such as a second HAPI server, get no overlay and stay as stored.

    Args:
        metadata_repository: The repository holding records and raw metadata.
        dataset_ids: The dataset IDs in the master catalog.

    Returns:
        The number of records saved again.
    """
    stored_records = metadata_repository.get_many(dataset_ids).values()
    records = [record for record in stored_records if describes_cdaweb_dataset(record)]
    save_with_overlays(metadata_repository, records)
    return len(records)
