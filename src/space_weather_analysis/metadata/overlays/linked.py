from space_weather_analysis.metadata.curated import CuratedLink
from space_weather_analysis.metadata.models import DatasetMetadata, MetadataSource, MetadataSourceKind, VariableMetadata

# The variable fields taken from the linked record. The fill value stays out, as it belongs to the encoding of the
# linked data files, not to the quantity.
LINKED_VARIABLE_FIELDS = ("description", "units", "label", "valid_min", "valid_max")


def apply_linked_overlay(record: DatasetMetadata, linked_record: DatasetMetadata, link: CuratedLink) -> None:
    """Fills the values a record lacks from the stored record of a dataset that holds the same quantities.

    Args:
        record: The metadata record, changed in place.
        linked_record: The stored record of the linked dataset.
        link: The link with the variable name in the linked record by field name of the record.
    """
    if record.links.spase_resource_id is None:
        record.links.spase_resource_id = linked_record.links.spase_resource_id
    # The landing page and documentation of the linked dataset become further documentation of the record
    for url in [linked_record.links.resource_url, *linked_record.links.documentation]:
        if url and url != record.links.resource_url and url not in record.links.documentation:
            record.links.documentation.append(url)

    linked_variables = {variable.name: variable for variable in linked_record.variables}
    # Each linked field takes the values of its linked variable where every other source left it without one
    for variable in record.variables:
        linked_variable = linked_variables.get(link.fields.get(variable.name, ""))
        if linked_variable is not None:
            _fill_from_linked(variable, linked_variable)

    linked_names = ", ".join(f"{field} as {linked_name}" for field, linked_name in sorted(link.fields.items()))
    record.replace_provenance(
        MetadataSource(
            kind=MetadataSourceKind.LINKED,
            url=linked_record.links.resource_url,
            retrieved_at=linked_record.updated_at,
            covers=f"metadata record of dataset {linked_record.dataset_id}: SPASE resource ID, links and the variables {linked_names}",
        )
    )


def _fill_from_linked(variable: VariableMetadata, linked_variable: VariableMetadata) -> None:
    """Sets the fields of a variable that have no value or a linked one from the linked variable and attributes them to "linked".

    Args:
        variable: The record variable, changed in place.
        linked_variable: The variable of the linked record holding the same quantity.
    """
    for field_name in LINKED_VARIABLE_FIELDS:
        value = getattr(linked_variable, field_name)
        is_replaceable = getattr(variable, field_name) is None or variable.field_sources.get(field_name) == MetadataSourceKind.LINKED
        if value is not None and is_replaceable:
            setattr(variable, field_name, value)
            variable.field_sources[field_name] = MetadataSourceKind.LINKED
