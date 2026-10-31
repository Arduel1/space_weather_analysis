import logging
from itertools import batched

from catalog_collector.hapi_mirror.servers import describes_hapi_source, first_hapi_servers, normalize_server_url
from space_weather_analysis.catalog.models import CatalogEntryDict, SourceType
from space_weather_analysis.catalog.repository import CatalogRepository
from space_weather_analysis.metadata.hapi import parse_hapi_time
from space_weather_analysis.metadata.models import DatasetMetadata
from space_weather_analysis.metadata.overlays.layering import save_with_overlays
from space_weather_analysis.metadata.repository import MetadataRepository

logger = logging.getLogger(__name__)

# Records compared and written per round trip to the database.
BATCH_SIZE = 1000


def sync_hapi_coverage(catalog_repository: CatalogRepository, metadata_repository: MetadataRepository) -> int:
    """Sets the coverage of each HAPI record to the time range the availability refresh stored for its catalog source.

    The availability refresh reads the mirror's dataset table, whose normalized dates also parse when a server
    wrote day-of-year dates, so its time range is the more reliable coverage of the record's server.

    Args:
        catalog_repository: The master catalog holding the refreshed time ranges.
        metadata_repository: The records to update.

    Returns:
        The number of records whose coverage changed.
    """
    sources = _refreshed_record_sources(catalog_repository)
    changed_count = 0
    for batch in batched(sorted(sources), BATCH_SIZE):
        stored_records = metadata_repository.get_many(batch)
        changed: list[DatasetMetadata] = []
        for dataset_id in batch:
            record = stored_records.get(dataset_id)
            source = sources[dataset_id]
            if record is None or not describes_hapi_source(record, source["source_id"]):
                continue
            coverage = (parse_hapi_time(source["time_range"]["start_time"]), parse_hapi_time(source["time_range"]["end_time"]))
            if None in coverage or coverage == (record.time.coverage_start, record.time.coverage_stop):
                continue
            record.time.coverage_start, record.time.coverage_stop = coverage
            changed.append(record)
        save_with_overlays(metadata_repository, changed)
        changed_count += len(changed)
    logger.info("HAPI coverage sync: %d records took the refreshed time range of their source.", changed_count)
    return changed_count


def _refreshed_record_sources(catalog_repository: CatalogRepository) -> dict[str, CatalogEntryDict]:
    """Finds for each dataset the catalog source of the HAPI server its record describes, if the availability refresh found it.

    Args:
        catalog_repository: The master catalog.

    Returns:
        The refreshed source of the record server, keyed by dataset ID.
    """
    record_servers = first_hapi_servers(catalog_repository)
    sources: dict[str, CatalogEntryDict] = {}
    for dataset_id, catalog_sources in catalog_repository.get_source_map(SourceType.HAPI).items():
        record_server = record_servers.get(dataset_id)
        for source in catalog_sources:
            is_record_server = record_server is not None and normalize_server_url(source["source_id"]) == normalize_server_url(record_server)
            if is_record_server and "availability_checked_at" in source:
                sources[dataset_id] = source
                break
    return sources
