import argparse
import functools
import logging
import pathlib
import shutil
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from space_weather_analysis.dashboard.config import CONFIG_ERRORS, load_dashboard_config, load_feed_datasets, load_feed_intervals, load_feed_schedule
from space_weather_analysis.dashboard.export import build_dashboard
from space_weather_analysis.dashboard.panels.base import PanelContext
from space_weather_analysis.dashboard.panels.images import MEDIA_DIRECTORY
from space_weather_analysis.dashboard.reading import RepositoryReader
from space_weather_analysis.database.connection import connect_from_environment
from space_weather_analysis.jobs.repository import MongoJobRepository
from space_weather_analysis.logging.config import setup_logging
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository
from space_weather_analysis.util.atomic_files import write_text_atomic
from space_weather_analysis.util.time_utils import parse_iso_duration

logger = logging.getLogger(__name__)

# The directory of the site holding the documents, which the web frontend reads below this path
DATA_DIRECTORY = "data"
# The layout document in the data directory, which names the payload of each panel
LAYOUT_FILE = "dashboard.json"
# The directory of the panel payloads in the data directory, one `{panel id}.json` each
PANELS_DIRECTORY = "panels"
# Files of the web frontend that only build its vendor directory and are not part of the served site
WEB_BUILD_FILES = ("node_modules", "package.json", "package-lock.json", "build-vendor.sh")


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    """Parses the command line."""
    parser = argparse.ArgumentParser(description="Writes the configured dashboard as a static site: the web frontend and its JSON documents.")
    parser.add_argument("--output", type=pathlib.Path, required=True, help="site directory; the documents go to its data/ directory")
    parser.add_argument("--web", type=pathlib.Path, help="directory of the web frontend, copied into the site; omitted writes only the documents")
    parser.add_argument(
        "--feeds-file", type=pathlib.Path, default=pathlib.Path("config/feeds.toml"), help="feed file naming the datasets kept current"
    )
    parser.add_argument(
        "--dashboard-dir", type=pathlib.Path, default=pathlib.Path("config/dashboard"), help="directory of the page order and page files"
    )
    parser.add_argument("--media", type=pathlib.Path, default=pathlib.Path("data/media"), help="stored frames of the image sequences")
    parser.add_argument("--serve", type=int, metavar="PORT", help="serve the site on this port and write the documents again every interval")
    parser.add_argument("--interval", default="PT1M", help="ISO 8601 duration between two writes while serving")
    return parser.parse_args(argv)


def export_documents(
    reader: RepositoryReader, dashboard_dir: pathlib.Path, feeds_file: pathlib.Path, data_dir: pathlib.Path, media_dir: pathlib.Path
) -> int:
    """Reads the dashboard configuration and the stored data and writes the layout, one payload per panel and the media files they show.

    The configuration is read on every call, so a changed dashboard shows with the next write.

    Args:
        reader: The reader of data, metadata and jobs, kept between writes so that long histories are read once an hour.
        dashboard_dir: The directory of the page order and page files.
        feeds_file: The feed file naming the datasets kept current.
        data_dir: The directory to write the documents into.
        media_dir: The directory of the stored frames of the image sequences.

    Returns:
        The number of panel documents written.

    Raises:
        OSError: If a configuration file cannot be read or a document cannot be written.
        tomllib.TOMLDecodeError: If a configuration file is no valid TOML.
        pydantic.ValidationError: If the dashboard configuration is invalid.
        pymongo.errors.PyMongoError: If the database cannot be read.
    """
    config = load_dashboard_config(dashboard_dir, feeds_file)
    context = PanelContext(
        now=datetime.now(UTC),
        feed_intervals=load_feed_intervals(feeds_file),
        feed_schedule=load_feed_schedule(feeds_file),
        feed_datasets=load_feed_datasets(feeds_file),
    )
    layout, payloads = build_dashboard(config, reader, context)
    _copy_media(media_dir, data_dir / MEDIA_DIRECTORY, {file for payload in payloads.values() for file in payload.media_files()})
    panels_dir = data_dir / PANELS_DIRECTORY
    panels_dir.mkdir(parents=True, exist_ok=True)
    for panel_id, payload in payloads.items():
        write_text_atomic(panels_dir / f"{panel_id}.json", payload.model_dump_json())
    # The layout last, so it names only panel documents that are already written
    write_text_atomic(data_dir / LAYOUT_FILE, layout.model_dump_json())
    return len(payloads)


def _copy_media(source_dir: pathlib.Path, target_dir: pathlib.Path, files: set[tuple[str, str]]) -> None:
    """Copies the media files the payloads show into the site and removes the site media files no payload shows any more.

    Files already in the site are not copied again, as a stored frame never changes; a file missing in the source is
    skipped, so its frame shows no image.

    Args:
        source_dir: The directory of the stored frames, one directory per dataset.
        target_dir: The media directory of the site.
        files: The shown files as pairs of dataset ID and file name.
    """
    for dataset, name in files:
        source, target = source_dir / dataset / name, target_dir / dataset / name
        if target.exists() or not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shown = {target_dir / dataset / name for dataset, name in files}
    for path in target_dir.glob("*/*"):
        if path not in shown:
            path.unlink()


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


def _rewrite_every(
    reader: RepositoryReader,
    dashboard_dir: pathlib.Path,
    feeds_file: pathlib.Path,
    data_dir: pathlib.Path,
    media_dir: pathlib.Path,
    interval: timedelta,
) -> None:
    """Writes the documents again every interval until the process ends; a failed write is logged and tried again next time."""
    while True:
        time.sleep(interval.total_seconds())
        try:
            export_documents(reader, dashboard_dir, feeds_file, data_dir, media_dir)
        except Exception:
            # The served site keeps its previous documents until the next write succeeds
            logger.exception("Writing the dashboard documents failed")


def main(argv: list[str] | None = None) -> None:
    """Writes the site once, or serves it and writes its documents again every interval.

    Args:
        argv: The command line arguments; None reads them from the process.

    Raises:
        SystemExit: With status 1 if the arguments or the dashboard configuration are invalid, or if the database is unreachable or
            fails on the first write.
    """
    setup_logging(service_name="SiteExport")
    arguments = _arguments(argv)
    interval = parse_iso_duration(arguments.interval)
    if interval is None:
        logger.critical("The interval %s is no ISO 8601 duration. Exiting.", arguments.interval)
        sys.exit(1)
    try:
        load_dashboard_config(arguments.dashboard_dir, arguments.feeds_file)
    except CONFIG_ERRORS as error:
        logger.critical("Cannot read the dashboard configuration of %s and %s: %s. Exiting.", arguments.dashboard_dir, arguments.feeds_file, error)
        sys.exit(1)

    if arguments.web is not None:
        shutil.copytree(arguments.web, arguments.output, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*WEB_BUILD_FILES))
    data_dir = arguments.output / DATA_DIRECTORY
    connection, db = connect_from_environment()
    try:
        reader = RepositoryReader(MongoTimeSeriesRepository(db), MongoMetadataRepository(db), MongoJobRepository(db))
        count = export_documents(reader, arguments.dashboard_dir, arguments.feeds_file, data_dir, arguments.media)
        logger.info("Wrote the layout and %d panel documents to %s", count, data_dir)
        if arguments.serve is not None:
            _serve(arguments.output, arguments.serve)
            _rewrite_every(reader, arguments.dashboard_dir, arguments.feeds_file, data_dir, arguments.media, interval)
    except KeyboardInterrupt:
        logger.info("Site export stopped.")
    except Exception:
        logger.exception("Writing the dashboard documents failed. Exiting.")
        sys.exit(1)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
