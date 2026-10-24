import logging
import sys


def setup_logging(service_name: str, level: int = logging.INFO) -> None:
    """
    Configures the root logger with a standardized format for all services.

    This setup ensures that logs from different microservices (Catalog, Pipeline,
    Frontend) share a consistent structure, making debugging and log aggregation
    easier. It directs all logs to the standard output (stdout).

    Args:
        service_name (str): The name of the service. This name will appear in every
                            log message to identify the source.
        level (int): The logging threshold level as a numeric level of the `logging` module.
                     Defaults to logging.INFO.
    """
    # Define the format string to include time, level, service name, and message.
    log_format = f"%(asctime)s [%(levelname)s] [{service_name}] [%(name)s] %(message)s"

    # Configure the root logger.
    logging.basicConfig(
        level=level,
        format=log_format,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,  # Ensures that any existing configuration is overwritten.
    )

    # Set the log level for external libraries to WARNING to reduce noise.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    logging.getLogger("fido").setLevel(logging.WARNING)
    logging.getLogger("hapiclient").setLevel(logging.WARNING)

    root_logger = logging.getLogger()
    root_logger.info(f"Logging initialized for service: {service_name}")
