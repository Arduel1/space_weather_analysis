import argparse
import functools
import logging
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pymongo.database import Database

from space_weather_analysis.dashboard.config import CONFIG_ERRORS, load_dashboard_config, load_feed_intervals, load_feed_schedule
from space_weather_analysis.dashboard.export import build_dashboard
from space_weather_analysis.dashboard.panels.base import PanelContext
from space_weather_analysis.dashboard.reading import RepositoryReader
from space_weather_analysis.database.connection import connect_from_environment
from space_weather_analysis.jobs.repository import MongoJobRepository
from space_weather_analysis.logging.config import setup_logging
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository
from space_weather_analysis.util.time_utils import parse_iso_duration

logger = logging.getLogger(__name__)

# The directory of the site holding the documents, which the web frontend reads below this path
DATA_DIRECTORY = "data"
# The layout document in the data directory, which names the payload of each panel
LAYOUT_FILE = "dashboard.json"
# The directory of the panel payloads in the data directory, one `{panel id}.json` each
PANELS_DIRECTORY = "panels"


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    """Parses the command line."""
    parser = argparse.ArgumentParser(description="Writes the dashboard of the feed file as a static site: the web frontend and its JSON documents.")
    parser.add_argument("--output", type=pathlib.Path, required=True, help="site directory; the documents go to its data/ directory")
    parser.add_argument("--web", type=pathlib.Path, help="directory of the web frontend, copied into the site; omitted writes only the documents")
    parser.add_argument("--feeds-file", type=pathlib.Path, default=pathlib.Path("config/feeds.toml"), help="feed file with the dashboard table")
    parser.add_argument("--serve", type=int, metavar="PORT", help="serve the site on this port and write the documents again every interval")
    parser.add_argument("--interval", default="PT1M", help="ISO 8601 duration between two writes while serving")
    return parser.parse_args(argv)


def _write_atomic(path: pathlib.Path, text: str) -> None:
    """Writes a file through a temporary file in its directory, so a reader never sees it half written."""
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
        temporary.write(text)
    # The temporary file is readable only by its owner; the site files get the usual permissions of served files
    os.chmod(temporary_name, 0o644)
    os.replace(temporary_name, path)


def export_documents(db: Database, feeds_file: pathlib.Path, data_dir: pathlib.Path) -> int:
    """Reads the dashboard table and the stored data and writes the layout and one payload per panel.

    The feed file is read on every call, so a changed dashboard shows with the next write.

    Args:
        db: The database holding data, metadata and jobs.
        feeds_file: The feed file with the dashboard table.
        data_dir: The directory to write the documents into.

    Returns:
        The number of panel documents written.

    Raises:
        OSError: If the feed file cannot be read or a document cannot be written.
        tomllib.TOMLDecodeError: If the feed file is no valid TOML.
        pydantic.ValidationError: If the dashboard table is invalid.
        pymongo.errors.PyMongoError: If the database cannot be read.
    """
    config = load_dashboard_config(feeds_file)
    context = PanelContext(now=datetime.now(UTC), feed_intervals=load_feed_intervals(feeds_file), feed_schedule=load_feed_schedule(feeds_file))
    reader = RepositoryReader(MongoTimeSeriesRepository(db), MongoMetadataRepository(db), MongoJobRepository(db))
    layout, payloads = build_dashboard(config, reader, context)
    panels_dir = data_dir / PANELS_DIRECTORY
    panels_dir.mkdir(parents=True, exist_ok=True)
    for panel_id, payload in payloads.items():
        _write_atomic(panels_dir / f"{panel_id}.json", payload.model_dump_json())
    # The layout last, so it names only panel documents that are already written
    _write_atomic(data_dir / LAYOUT_FILE, layout.model_dump_json())
    return len(payloads)


class _QuietHandler(SimpleHTTPRequestHandler):
    """Serves the files of the site and logs each request only at debug level."""

    def log_message(self, format: str, *args: Any) -> None:
        """Logs one request at debug level instead of writing it to stderr."""
        logger.debug(format, *args)


def _serve(site_dir: pathlib.Path, port: int) -> None:
    """Serves the site directory on a port in a daemon thread, which ends with the process."""
    server = ThreadingHTTPServer(("", port), functools.partial(_QuietHandler, directory=str(site_dir)))
    threading.Thread(target=server.serve_forever, name="site-server", daemon=True).start()
    logger.info("Serving the dashboard from %s on port %d", site_dir, port)


def _rewrite_every(db: Database, feeds_file: pathlib.Path, data_dir: pathlib.Path, interval: timedelta) -> None:
    """Writes the documents again every interval until the process ends; a failed write is logged and tried again next time."""
    while True:
        time.sleep(interval.total_seconds())
        try:
            export_documents(db, feeds_file, data_dir)
        except Exception:
            # The served site keeps its previous documents until the next write succeeds
            logger.exception("Writing the dashboard documents failed")


def main(argv: list[str] | None = None) -> None:
    """Writes the site once, or serves it and writes its documents again every interval.

    Args:
        argv: The command line arguments; None reads them from the process.

    Raises:
        SystemExit: With status 1 if the arguments or the feed file are invalid, or if the database is unreachable or
            fails on the first write.
    """
    setup_logging(service_name="SiteExport")
    arguments = _arguments(argv)
    interval = parse_iso_duration(arguments.interval)
    if interval is None:
        logger.critical("The interval %s is no ISO 8601 duration. Exiting.", arguments.interval)
        sys.exit(1)
    try:
        load_dashboard_config(arguments.feeds_file)
    except CONFIG_ERRORS as error:
        logger.critical("Cannot read the dashboard of the feed file %s: %s. Exiting.", arguments.feeds_file, error)
        sys.exit(1)

    if arguments.web is not None:
        shutil.copytree(arguments.web, arguments.output, dirs_exist_ok=True)
    data_dir = arguments.output / DATA_DIRECTORY
    connection, db = connect_from_environment()
    try:
        count = export_documents(db, arguments.feeds_file, data_dir)
        logger.info("Wrote the layout and %d panel documents to %s", count, data_dir)
        if arguments.serve is not None:
            _serve(arguments.output, arguments.serve)
            _rewrite_every(db, arguments.feeds_file, data_dir, interval)
    except KeyboardInterrupt:
        logger.info("Site export stopped.")
    except Exception:
        logger.exception("Writing the dashboard documents failed. Exiting.")
        sys.exit(1)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
