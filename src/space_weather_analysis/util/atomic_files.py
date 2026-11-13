import os
import pathlib
import secrets
from collections.abc import Generator
from contextlib import contextmanager
from typing import BinaryIO

# Mode bits a written file is created with, which the umask of the process narrows like for any created file:
# writable by its owner and readable by everyone, as the HTTP server and the other containers read the files
FILE_MODE = 0o644


def _create_temporary(path: pathlib.Path) -> tuple[int, pathlib.Path]:
    """Creates a new hidden temporary file next to a path, open for writing.

    tempfile.mkstemp creates its file readable only by its owner, so the file is created here with FILE_MODE and the
    kernel applies the umask; reading the umask would need to set it, which is not thread-safe.

    Args:
        path: The file the temporary file is to replace.

    Returns:
        The descriptor and the path of the temporary file.

    Raises:
        OSError: If the file cannot be created.
    """
    while True:
        # A random name that exists already is drawn again; O_EXCL makes the creation fail instead of opening it
        candidate = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
        try:
            return os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE), candidate
        except FileExistsError:
            continue


@contextmanager
def atomic_file(path: pathlib.Path) -> Generator[BinaryIO]:
    """Opens a temporary file next to a path that replaces the file at the path once the block finishes without error.

    A reader never sees a partly written file, and a failure keeps the previous file and removes the temporary one.

    Args:
        path: The file to replace; its directory must exist.

    Yields:
        The temporary file, open for writing bytes.

    Raises:
        OSError: If the temporary file cannot be created, written or moved to the path.
    """
    descriptor, temporary_path = _create_temporary(path)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            yield temporary
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def write_text_atomic(path: pathlib.Path, text: str) -> None:
    """Writes a text file as UTF-8 through a temporary file, so a reader never sees it partly written.

    Args:
        path: The file to write; its directory must exist.
        text: The content.

    Raises:
        OSError: If the file cannot be written.
    """
    with atomic_file(path) as file:
        file.write(text.encode("utf-8"))
