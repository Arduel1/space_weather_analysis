from pydantic import ValidationError


def format_settings_error(error: ValidationError) -> str:
    """Summarizes a settings validation error without the input values.

    The default string representation of a pydantic ValidationError contains
    the input values, which may include secrets. This summary only names
    the affected fields and the reasons, so it is safe to log.

    Args:
        error (ValidationError): The error raised while constructing the settings.

    Returns:
        str: The affected fields and error messages, separated by semicolons.
    """
    parts = []
    for detail in error.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in detail["loc"])
        parts.append(f"{location}: {detail['msg']}")
    return "; ".join(parts)
