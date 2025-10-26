import logging
import os

from typing import Optional
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import ConnectionFailure

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """
    A Singleton class to manage a single MongoDB connection.

    This class ensures that only one instance of the MongoClient is
    created ("lazy loading") and provides methods to access the client
    and a specific database.
    """

    _client: Optional[MongoClient] = None
    _db: Optional[Database] = None

    @classmethod
    def _connect(cls) -> Optional[MongoClient]:
        """
        Internal method to establish the connection if it doesn't exist.
        """
        if cls._client:
            return cls._client

        MONGODB_URI = f"mongodb://{os.getenv('MONGO_USER')}:{os.getenv('MONGO_PASS')}@mongo:{os.getenv('MONGO_PORT')}/"

        try:
            logger.info("Attempting to connect to MongoDB...")
            cls._client = MongoClient(
                MONGODB_URI,
                serverSelectionTimeoutMS=10000,
                socketTimeoutMS=60000,
                connectTimeoutMS=60000,
            )
            # Test the connection
            cls._client.admin.command("ping")
            logger.info("MongoDB connection successful.")
            return cls._client

        except ConnectionFailure as e:
            logger.critical(f"Failed to connect to MongoDB: {e}")
            cls._client = None
            return None
        except Exception as e:
            logger.critical(f"An unexpected error occurred during DB connection: {e}")
            cls._client = None
            return None

    @classmethod
    def get_database(cls) -> Optional[Database]:
        """
        Retrieves the MongoDB database instance.

        If a connection is not already established, this method will
        trigger the connection attempt.

        Returns:
            Optional[Database]: The database object or None if
                                connection fails or DB name is not set.
        """
        if cls._db is not None:
            return cls._db

        client = cls._connect()
        if not client:
            return None

        db_name = os.getenv("MONGO_DB_NAME")
        if not db_name:
            logger.error("MONGO_DB_NAME environment variable not set.")
            return None

        cls._db = client[db_name]
        return cls._db

    @classmethod
    def close(cls):
        """
        Closes the global MongoDB client connection, if it exists.
        """
        if cls._client:
            cls._client.close()
            cls._client = None
            cls._db = None
            logger.info("MongoDB connection closed.")
