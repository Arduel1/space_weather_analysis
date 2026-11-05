from collections.abc import Sequence

from space_weather_analysis.metadata.curated import configured_curated
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSourceKind
from space_weather_analysis.metadata.overlays.cdas import apply_cdas_overlay
from space_weather_analysis.metadata.overlays.cdaweb import cdas_dataset_id, describes_cdaweb_dataset
from space_weather_analysis.metadata.overlays.curated import apply_curated_overlay
from space_weather_analysis.metadata.overlays.istp import apply_istp_overlay
from space_weather_analysis.metadata.overlays.spase import apply_spase_overlay
from space_weather_analysis.metadata.repository import MetadataRepository


def save_with_overlays(metadata_repository: MetadataRepository, records: Sequence[DatasetMetadata]) -> None:
    """Saves records after laying the stored metadata of further sources over them.

    Every writer of normalized records saves through this function, so a record rebuilt from one
    source keeps the fields another source is authoritative for. CDAWeb records get the further sources in
    increasing rank: the SPASE record (raw kind "spase"), the master file (raw kind "istp") and the CDAS entry
    (raw kind "cdas"), so each source overwrites the ones ranked below it. Last, the curated metadata of the
    configured directory is laid over every record that it describes, as the highest-ranked source.

    Args:
        metadata_repository: The repository holding records and raw metadata.
        records: The records to save, changed in place by the overlays.

    Raises:
        ValueError: If a curated metadata file is invalid or describes a dataset another file describes too.
    """
    cdaweb_records = [record for record in records if describes_cdaweb_dataset(record)]
    if cdaweb_records:
        cdas_ids = sorted({cdas_dataset_id(record.dataset_id) for record in cdaweb_records})
        raw_spase_records = metadata_repository.get_raw_many(cdas_ids, MetadataSourceKind.SPASE)
        raw_masters = metadata_repository.get_raw_many(cdas_ids, MetadataSourceKind.ISTP)
        raw_cdas_entries = metadata_repository.get_raw_many(cdas_ids, MetadataSourceKind.CDAS)
        for record in cdaweb_records:
            cdas_id = cdas_dataset_id(record.dataset_id)
            raw_spase_record = raw_spase_records.get(cdas_id)
            if raw_spase_record is not None:
                apply_spase_overlay(record, raw_spase_record.payload, raw_spase_record.retrieved_at, raw_spase_record.url)
            raw_master = raw_masters.get(cdas_id)
            if raw_master is not None:
                apply_istp_overlay(record, raw_master.payload, raw_master.retrieved_at, raw_master.url)
            raw_cdas_entry = raw_cdas_entries.get(cdas_id)
            if raw_cdas_entry is not None:
                apply_cdas_overlay(record, raw_cdas_entry.payload, raw_cdas_entry.retrieved_at, raw_cdas_entry.url)
    curated = configured_curated()
    for record in records:
        curated_dataset = curated.get(record.dataset_id)
        if curated_dataset is not None:
            apply_curated_overlay(record, curated_dataset)
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
