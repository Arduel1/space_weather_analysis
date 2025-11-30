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
    created ("lazy loading") and provides class methods to access the
    client and a specific database.
    """

    _client: Optional[MongoClient] = None
    _db: Optional[Database] = None

    @classmethod
    def _connect(cls) -> Optional[MongoClient]:
        """
        Internal method to establish the connection if it doesn't exist.

        Validates necessary environment variables before attempting connection.

        Returns:
            Optional[MongoClient]: The active MongoClient instance, or None
                                   if connection fails or credentials are missing.
        """
        if cls._client:
            return cls._client

        # Fetch credentials safely
        mongo_user = os.getenv("MONGO_USER")
        mongo_pass = os.getenv("MONGO_PASS")
        mongo_port = os.getenv("MONGO_PORT")
        # Allow configuring the host, defaulting to 'mongo' for Docker compatibility
        mongo_host = os.getenv("MONGO_HOST", "mongo")

        if not all([mongo_user, mongo_pass, mongo_port]):
            logger.critical("Missing MongoDB credentials. Ensure MONGO_USER, MONGO_PASS, and MONGO_PORT are set.")
            return None

        # Construct URI using the configurable host
        mongodb_uri = f"mongodb://{mongo_user}:{mongo_pass}@{mongo_host}:{mongo_port}/"

        try:
            logger.info(f"Attempting to connect to MongoDB at {mongo_host}:{mongo_port}...")
            cls._client = MongoClient(
                mongodb_uri,
                serverSelectionTimeoutMS=5000,
                socketTimeoutMS=60000,
                connectTimeoutMS=10000,
            )
            # Test the connection immediately
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
            logger.critical("MONGO_DB_NAME environment variable not set.")
            return None

        cls._db = client[db_name]
        return cls._db

    @classmethod
    def close(cls) -> None:
        """
        Closes the global MongoDB client connection, if it exists.
        And resets the internal state to allow reconnection if needed.
        """
        if cls._client:
            cls._client.close()
            cls._client = None
            cls._db = None
            logger.info("MongoDB connection closed.")
