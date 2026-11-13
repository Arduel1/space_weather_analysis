from collections.abc import Sequence

from space_weather_analysis.metadata.curated import CuratedDataset, configured_curated
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSourceKind
from space_weather_analysis.metadata.overlays.cdas import apply_cdas_overlay
from space_weather_analysis.metadata.overlays.cdaweb import cdas_dataset_id, describes_cdaweb_dataset
from space_weather_analysis.metadata.overlays.curated import apply_curated_overlay
from space_weather_analysis.metadata.overlays.istp import apply_istp_overlay
from space_weather_analysis.metadata.overlays.linked import apply_linked_overlay
from space_weather_analysis.metadata.overlays.spase import apply_spase_overlay
from space_weather_analysis.metadata.repository import MetadataRepository


def save_with_overlays(metadata_repository: MetadataRepository, records: Sequence[DatasetMetadata]) -> None:
    """Saves records after laying the stored metadata of further sources over them, the highest-ranked source last.

    Every writer of normalized records saves through this function, so a record rebuilt from one source keeps the
    fields another source is authoritative for.

    Args:
        metadata_repository: The repository holding records and raw metadata.
        records: The records to save, changed in place by the overlays.

    Raises:
        ValueError: If a curated metadata file is invalid or describes a dataset another file describes too.
    """
    # CDAWeb records get the SPASE record, the master file and the CDAS entry in increasing rank, each overwriting the
    # ones before it
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
    linked_records = _linked_records(metadata_repository, records, curated)
    for record in records:
        curated_dataset = curated.get(record.dataset_id)
        if curated_dataset is None:
            continue
        # A dataset linked as having the same quantities fills the values the record lacks; without a stored linked
        # record nothing is taken until a later save finds one
        link = curated_dataset.same_as
        if link is not None and link.dataset in linked_records:
            apply_linked_overlay(record, linked_records[link.dataset], link)
        # The curated metadata is the highest-ranked source and goes last
        apply_curated_overlay(record, curated_dataset)
    metadata_repository.save_many(records)


def _linked_records(
    metadata_repository: MetadataRepository, records: Sequence[DatasetMetadata], curated: dict[str, CuratedDataset]
) -> dict[str, DatasetMetadata]:
    """Reads the records the curated metadata of the saved records links to, preferring a record saved in the same call.

    Args:
        metadata_repository: The repository holding the stored records.
        records: The records to save.
        curated: The curated metadata by dataset ID.

    Returns:
        The linked records by dataset ID; a linked dataset without record is missing.
    """
    linked_ids: set[str] = set()
    for record in records:
        curated_dataset = curated.get(record.dataset_id)
        if curated_dataset is not None and curated_dataset.same_as is not None:
            linked_ids.add(curated_dataset.same_as.dataset)
    if not linked_ids:
        return {}
    linked_records = metadata_repository.get_many(sorted(linked_ids))
    linked_records.update({record.dataset_id: record for record in records if record.dataset_id in linked_ids})
    return linked_records


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
