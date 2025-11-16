from typing import Any, Generator, List


def batch_generator(data_list: List[Any], batch_size: int = 5000) -> Generator[List[Any], None, None]:
    """
    A generator function that yields data in batches.

    This is used to split a large list of documents before a bulk
    database insertion to avoid timeouts or memory issues.

    Args:
        data_list (List[Any]): The complete list of items to be batched.
        batch_size (int): The maximum number of items to include in each
                          batch. Defaults to 5000.

    Yields:
        Generator[List[Any], None, None]: A generator that yields
                                          sub-lists (batches).
    """
    if not data_list:
        return

    for i in range(0, len(data_list), batch_size):
        yield data_list[i : i + batch_size]


def sanitize_collection_name(dataset_id: str) -> str:
    """
    Replaces characters in a string that are invalid for MongoDB collection names.

    MongoDB collection names should not contain certain characters like
    '/' or '-'. This utility replaces them with underscores.

    Args:
        dataset_id (str): The raw string (e.g., dataset ID) to sanitize.

    Returns:
        str: A sanitized string suitable for use as a collection name.
    """
    return dataset_id.replace("/", "_").replace("-", "_")
