import pathlib
from collections.abc import Sequence
from datetime import datetime, timedelta

import pandas as pd

# How long a stored map or model file of the dashboard animations stays: the day the animation shows, and some hours more
MEDIA_KEEP = timedelta(days=1, hours=6)
# Format of the UTC time that starts the name of a stored media file; such names sort like the times they name
TIME_NAME_FORMAT = "%Y%m%dT%H%M%SZ"


def timestamped_name(time: datetime, rest: str) -> str:
    """Names a stored media file by its UTC time followed by the rest of the name.

    Args:
        time: The time the file stands for, in UTC.
        rest: The text after the time, ending with the file extension.

    Returns:
        The file name.
    """
    return f"{time:{TIME_NAME_FORMAT}}{rest}"


def remove_files_before(directory: pathlib.Path, extension: str, cutoff: datetime) -> None:
    """Deletes the files of a directory with the extension whose names start with a time before the cutoff.

    Args:
        directory: The directory of the files.
        extension: The file extension with its leading dot.
        cutoff: The oldest time kept, in UTC.
    """
    cutoff_name = timestamped_name(cutoff, extension)
    for path in directory.glob(f"*{extension}"):
        # File names sort like the times they name
        if path.name < cutoff_name:
            path.unlink()


def name_times(names: Sequence[str], extension: str) -> pd.DatetimeIndex:
    """Reads the UTC times back from file names that hold only the time and the extension.

    Args:
        names: The file names.
        extension: The file extension with its leading dot.

    Returns:
        The time of each name in UTC.
    """
    return pd.to_datetime([name.removesuffix(extension) for name in names], format=TIME_NAME_FORMAT, utc=True)
