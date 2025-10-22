import logging
import os

from pymongo import MongoClient



MONGODB_URI=f"mongodb://{os.getenv("MONGO_USER")}:{os.getenv("MONGO_PASS")}@mongo:{os.getenv("MONGO_PORT")}/"
logger = logging.getLogger(__name__)

# MongoDB setup
mongo_client = MongoClient(
    MONGODB_URI,
    # Increase timeout for heavy load from bigger files
    serverSelectionTimeoutMS=60000,
    socketTimeoutMS=60000,
    connectTimeoutMS=60000,
)


def get_database():
    """
    Simple function to get the database object.
    """
    client = mongo_client
    db_name = os.getenv("MONGO_DB_NAME")
    if not db_name:
        logger.error("MONGO_DB_NAME environment variable not set.")
        return None
    
    return client[db_name]