import logging
import sys

# Libraries whose informational output is reduced to warnings; SunPy downloads through parfive.
QUIET_LIBRARY_LOGGERS = ("urllib3", "pymongo", "hapiclient", "sunpy", "parfive")


def setup_logging(service_name: str, level: int = logging.INFO) -> None:
    """Configures the root logger with one format for all services, writing to standard output.

    Args:
        service_name: The name of the service, shown in every log line.
        level: The logging threshold as a numeric level of the `logging` module.
    """
    logging.basicConfig(
        level=level,
        format=f"%(asctime)s [%(levelname)s] [{service_name}] [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        # A previous configuration, such as one made by an imported library, is replaced
        force=True,
    )

    for logger_name in QUIET_LIBRARY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    logging.getLogger().info("Logging initialized for service: %s", service_name)
