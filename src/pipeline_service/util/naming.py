def sanitize_collection_name(dataset_id: str) -> str:
    """Replaces every '/' and '-' in a dataset ID with an underscore.

    The pipelines use the result as the name of the MongoDB collection that
    stores the dataset.

    Args:
        dataset_id: The raw dataset ID to sanitize.

    Returns:
        A sanitized string suitable for use as a collection name.
    """
    return dataset_id.replace("/", "_").replace("-", "_")
