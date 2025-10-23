import logging
import sys
from dotenv import load_dotenv

from space_weather_analysis.database.connection import get_database
from space_weather_analysis.etl.sunspots import etl_silso_data

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main():
    """
    Main entry point for the application.
    """
    logger.info("--- Application started ---")

    db = get_database()
    if db is None:
        logger.critical("Could not get database connection. Exiting.")
        sys.exit(1)

    logger.info(f"Successfully connected to database: {db.name}")

    etl_silso_data(db)

    try:
        collections = db.list_collection_names()
        logger.info(f"Current collections: {collections}")
    except Exception as e:
        logger.error(f"Error listing collections: {e}")

    logger.info("--- Application finished ---")


if __name__ == "__main__":
    main()
