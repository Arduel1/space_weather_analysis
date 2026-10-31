import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from space_weather_analysis.metadata.models import MetadataSourceKind
from space_weather_analysis.metadata.overlays.layering import resave_cdaweb_records
from space_weather_analysis.metadata.repository import MetadataRepository, sanitize_for_bson

logger = logging.getLogger(__name__)


def store_changed_raw_and_overlay(
    metadata_repository: MetadataRepository,
    kind: MetadataSourceKind,
    cdas_id: str,
    payload: dict[str, Any],
    url: str,
    retrieved_at: datetime,
    dataset_ids: Sequence[str],
) -> bool:
    """Stores the raw metadata of one CDAWeb dataset if its content changed and saves the dataset's records again with the overlays.

    Providers regenerate their files without content changes, so only a changed content counts as change.

    Args:
        metadata_repository: The raw metadata and records.
        kind: The kind of source of the payload.
        cdas_id: The CDAS dataset ID under which the raw metadata is stored.
        payload: The metadata as read.
        url: The location the payload was read from.
        retrieved_at: When the payload was read.
        dataset_ids: The catalog datasets that share the CDAS dataset ID.

    Returns:
        True if the stored raw metadata changed.
    """
    sanitized_payload = sanitize_for_bson(payload)
    stored = metadata_repository.get_raw(cdas_id, kind)
    if stored is not None and stored.payload == sanitized_payload:
        return False

    # The raw metadata comes first, as the overlay of the saved records reads it
    metadata_repository.save_raw(cdas_id, kind, sanitized_payload, url, retrieved_at)
    overlaid = resave_cdaweb_records(metadata_repository, dataset_ids)
    logger.info("Raw %s metadata of %s saved; %d records overlaid.", kind, cdas_id, overlaid)
    return True
