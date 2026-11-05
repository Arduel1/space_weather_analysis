import argparse
import logging
import pathlib
import sys
from datetime import UTC, datetime

from space_weather_analysis.dashboard.config import CONFIG_ERRORS, load_dashboard_config, load_feed_intervals, load_feed_schedule
from space_weather_analysis.dashboard.export import build_dashboard
from space_weather_analysis.dashboard.panels.base import PanelContext
from space_weather_analysis.dashboard.reading import RepositoryReader
from space_weather_analysis.database.connection import connect_from_environment
from space_weather_analysis.jobs.repository import MongoJobRepository
from space_weather_analysis.logging.config import setup_logging
from space_weather_analysis.metadata.repository import MongoMetadataRepository
from space_weather_analysis.timeseries.repository import MongoTimeSeriesRepository

logger = logging.getLogger(__name__)

# The layout document in the output directory, which names the payload of each panel
LAYOUT_FILE = "dashboard.json"
# The directory of the panel payloads, one `{panel id}.json` each
PANELS_DIRECTORY = "panels"


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    """Parses the command line."""
    parser = argparse.ArgumentParser(description="Writes the dashboard of the feed file as JSON documents for a static web frontend.")
    parser.add_argument("--output", type=pathlib.Path, required=True, help="directory to write the documents into")
    parser.add_argument("--feeds-file", type=pathlib.Path, default=pathlib.Path("config/feeds.toml"), help="feed file with the dashboard table")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Reads the dashboard table and the stored data once and writes the layout and one payload per panel.

    Args:
        argv: The command line arguments; None reads them from the process.

    Raises:
        SystemExit: With status 1 if the feed file is invalid or the database is unreachable or fails.
    """
    setup_logging(service_name="SiteExport")
    arguments = _arguments(argv)
    try:
        config = load_dashboard_config(arguments.feeds_file)
        context = PanelContext(
            now=datetime.now(UTC), feed_intervals=load_feed_intervals(arguments.feeds_file), feed_schedule=load_feed_schedule(arguments.feeds_file)
        )
    except CONFIG_ERRORS as error:
        logger.critical("Cannot read the dashboard of the feed file %s: %s. Exiting.", arguments.feeds_file, error)
        sys.exit(1)

    connection, db = connect_from_environment()
    try:
        reader = RepositoryReader(MongoTimeSeriesRepository(db), MongoMetadataRepository(db), MongoJobRepository(db))
        layout, payloads = build_dashboard(config, reader, context)
    except Exception:
        logger.exception("Building the dashboard documents failed. Exiting.")
        sys.exit(1)
    finally:
        connection.close()

    panels_directory = arguments.output / PANELS_DIRECTORY
    panels_directory.mkdir(parents=True, exist_ok=True)
    (arguments.output / LAYOUT_FILE).write_text(layout.model_dump_json(), encoding="utf-8")
    for panel_id, payload in payloads.items():
        (panels_directory / f"{panel_id}.json").write_text(payload.model_dump_json(), encoding="utf-8")
    logger.info("Wrote the layout and %d panel documents to %s", len(payloads), arguments.output)


if __name__ == "__main__":
    main()
