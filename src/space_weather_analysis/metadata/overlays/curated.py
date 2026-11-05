from datetime import UTC, datetime, time
from typing import Any

from space_weather_analysis.metadata.curated import CuratedDataset
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSource, MetadataSourceKind


def _set_given(target: Any, values: dict[str, Any]) -> list[str]:
    """Sets every attribute of a model whose curated value is given and returns the names of the set attributes."""
    set_names = []
    for name, value in values.items():
        if value is not None:
            setattr(target, name, value)
            set_names.append(name)
    return set_names


def apply_curated_overlay(record: DatasetMetadata, curated: CuratedDataset) -> None:
    """Sets every value the curated metadata gives, the highest-ranked source, over the values of all other sources.

    Curated fields describe only variables the record has, as the record lists the stored fields. The pages the
    curated values were taken from become the curated provenance entries, replacing earlier ones.

    Args:
        record: The metadata record, changed in place.
        curated: The curated metadata of the dataset.
    """
    _set_given(record, {"title": curated.title, "description": curated.description})
    _set_given(record.source, {"provider": curated.provider})
    _set_given(record.links, {"resource_url": curated.resource_url})
    _set_given(record.citation, curated.citation.model_dump())
    _set_given(record.time, curated.time.model_dump())

    curated_variables = {variable.name: variable for variable in curated.variable}
    for variable in record.variables:
        curated_variable = curated_variables.get(variable.name)
        if curated_variable is None:
            continue
        for name in _set_given(variable, curated_variable.model_dump(exclude={"name"})):
            variable.field_sources[name] = MetadataSourceKind.CURATED

    record.provenance = [entry for entry in record.provenance if entry.kind != MetadataSourceKind.CURATED]
    record.provenance.extend(
        MetadataSource(kind=MetadataSourceKind.CURATED, url=reference.url, retrieved_at=datetime.combine(reference.retrieved, time(), UTC))
        for reference in curated.reference
    )
