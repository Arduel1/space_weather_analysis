import logging
import pathlib
import signal

logger = logging.getLogger(__name__)

# Touched by the main loop of a long-running service; the healthcheck in docker-compose.yml reports the container
# unhealthy once the file is older than a few minutes
HEARTBEAT_FILE = pathlib.Path("/tmp/heartbeat")


def stop_on_termination() -> None:
    """Lets SIGTERM end the main thread like Ctrl+C, so that the service shuts down through its KeyboardInterrupt handling.

    Docker sends SIGTERM on `docker stop` and kills the process ten seconds later; Python itself ends on SIGTERM without
    running any cleanup. Must be called from the main thread.
    """
    signal.signal(signal.SIGTERM, signal.default_int_handler)


def beat(path: pathlib.Path = HEARTBEAT_FILE) -> None:
    """Sets the modification time of the heartbeat file to now, creating the file if needed.

    A failed write only shows up as unhealthy container, so it is logged and never ends the service.

    Args:
        path: The heartbeat file.
    """
    try:
        path.touch()
    except OSError as error:
        logger.warning("Heartbeat file %s not written: %s", path, error)
