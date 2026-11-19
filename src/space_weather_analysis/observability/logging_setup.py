import json
import logging
import sys
from datetime import UTC, datetime
from typing import Literal

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from space_weather_analysis.config.errors import format_settings_error

# Libraries whose informational output is reduced to warnings; SunPy downloads through parfive.
QUIET_LIBRARY_LOGGERS = ("urllib3", "pymongo", "hapiclient", "sunpy", "parfive")

# Attributes every log record has; the JSON format writes any other attribute, given through `extra`, as its own field
STANDARD_RECORD_ATTRIBUTES = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime", "taskName"}


class LoggingSettings(BaseSettings):
    """Settings of the log output of every service, read from the environment variables `LOG_{field name}` in upper case.

    Attributes:
        level: The lowest level written.
        format: `text` for one readable line per message, `json` for one JSON object per line for a log collector.
    """

    model_config = SettingsConfigDict(env_prefix="LOG_", frozen=True)

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    format: Literal["text", "json"] = "text"


class JsonFormatter(logging.Formatter):
    """Writes each record as one JSON object.

    The object holds time, level, service, logger, message, the fields given through `extra` and the traceback of an exception.
    """

    def __init__(self, service_name: str) -> None:
        """Creates the formatter.

        Args:
            service_name: The name of the service, written into every record.
        """
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        """Formats one record as a JSON line.

        Args:
            record: The log record.

        Returns:
            The JSON object without line breaks.
        """
        fields: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields.update({key: value for key, value in record.__dict__.items() if key not in STANDARD_RECORD_ATTRIBUTES})
        if record.exc_info:
            fields["exception"] = self.formatException(record.exc_info)
        # Values json cannot write, such as datetimes, are written as their text
        return json.dumps(fields, default=str)


def setup_logging(service_name: str) -> None:
    """Configures the root logger of a service with the level and format of the LOG_* settings, writing to standard output.

    Invalid LOG_* settings are logged as warning, and the defaults are used, so that a typo does not stop a service.

    Args:
        service_name: The name of the service, shown in every log line.
    """
    settings_error: ValidationError | None = None
    try:
        settings = LoggingSettings()
    except ValidationError as error:
        settings_error = error
        settings = LoggingSettings.model_construct()

    handler = logging.StreamHandler(sys.stdout)
    if settings.format == "json":
        handler.setFormatter(JsonFormatter(service_name))
    else:
        handler.setFormatter(logging.Formatter(f"%(asctime)s [%(levelname)s] [{service_name}] [%(name)s] %(message)s"))
    # force replaces a previous configuration, such as one made by an imported library
    logging.basicConfig(level=settings.level, handlers=[handler], force=True)

    for logger_name in QUIET_LIBRARY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    root_logger = logging.getLogger()
    if settings_error is not None:
        root_logger.warning("Invalid LOG_* settings, using level INFO and format text: %s", format_settings_error(settings_error))
    root_logger.info("Logging initialized for service %s at level %s in format %s", service_name, settings.level, settings.format)
