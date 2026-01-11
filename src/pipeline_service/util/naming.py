def sanitize_collection_name(dataset_id: str) -> str:
    """
    Replaces characters in a string that are invalid for MongoDB collection names.

    MongoDB collection names should not contain certain characters like
    '/' or '-'. This utility replaces them with underscores.

    Args:
        dataset_id: The raw string (e.g., dataset ID) to sanitize.

    Returns:
        A sanitized string suitable for use as a collection name.
    """
    return dataset_id.replace("/", "_").replace("-", "_")
