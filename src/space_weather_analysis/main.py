import logging
import sys
from dotenv import load_dotenv

from space_weather_analysis.database.connection import get_database
from space_weather_analysis.etl.cme_catalog import CmeETL
from space_weather_analysis.etl.sunspots import SilsoETL

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main():
    """
    Main entry point for the ETL application.

    This function orchestrates the entire pipeline:
    1. Sets up the database connection.
    2. Initializes and runs all registered ETL processors.
    3. Logs the final list of collections in the database.
    """
    logger.info("--- Application started ---")

    db = get_database()
    if db is None:
        logger.critical("Could not get database connection. Exiting.")
        sys.exit(1)

    logger.info(f"Successfully connected to database: {db.name}")

    # Run ETL processes sequentially
    SilsoETL(db).run()
    CmeETL(db).run()

    # Final check to log the state of the database
    try:
        collections = db.list_collection_names()
        logger.info(f"Current collections: {collections}")
    except Exception as e:
        logger.error(f"Error listing collections: {e}")

    logger.info("--- Application finished ---")


if __name__ == "__main__":
    main()
