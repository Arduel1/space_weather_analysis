import pathlib
import tomllib

from pydantic import BaseModel, ConfigDict

# Model configuration of every table of a configuration file: closed, so a mistyped key fails the validation instead of
# being ignored, and frozen, as the read configuration is not changed
CLOSED = ConfigDict(extra="forbid", frozen=True)


def load_toml[M: BaseModel](path: pathlib.Path, model: type[M]) -> M:
    """Reads a TOML configuration file into its model.

    Args:
        path: The path of the TOML file.
        model: The model of the whole file.

    Returns:
        The validated content.

    Raises:
        OSError: If the file cannot be read.
        tomllib.TOMLDecodeError: If the file is no valid TOML.
        pydantic.ValidationError: If the content does not match the model.
    """
    with path.open("rb") as file:
        return model.model_validate(tomllib.load(file))
