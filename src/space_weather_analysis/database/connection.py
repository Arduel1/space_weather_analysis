import logging
import os
from typing import Optional

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import ConnectionFailure

# Logger initialization for the database module
logger = logging.getLogger(__name__)


class DatabaseConnection:
    """
    Manages the global MongoDB connection using a Singleton-like pattern.

    This class ensures that only one database client instance is created
    and shared throughout the application lifecycle. It retrieves connection
    credentials from environment variables and handles the initialization
    and teardown of the connection.
    """

    _client: Optional[MongoClient] = None
    _db: Optional[Database] = None

    @classmethod
    def get_database(cls) -> Optional[Database]:
        """
        Retrieves the active MongoDB database instance.

        If a connection has not been established yet, this method attempts
        to create one using the configured environment variables. If a
        connection already exists, the cached instance is returned to
        minimize overhead.

        Returns:
            Optional[Database]: The MongoDB database object if the connection
            is successful, otherwise None.
        """
        if cls._db is not None:
            return cls._db

        try:
            mongo_user = os.getenv("MONGO_USER")
            mongo_pass = os.getenv("MONGO_PASS")
            mongo_host = os.getenv("MONGO_HOST", "mongo")
            mongo_port = os.getenv("MONGO_PORT", "27017")

            if not all([mongo_user, mongo_pass]):
                logger.error("Missing MongoDB credentials in environment variables.")
                return None

            connection_string = f"mongodb://{mongo_user}:{mongo_pass}@{mongo_host}:{mongo_port}"

            logger.info("Attempting to connect to MongoDB...")
            cls._client = MongoClient(connection_string, serverSelectionTimeoutMS=5000)

            # Verify that the server is actually reachable and authentication was successful.
            cls._client.admin.command("ping")
            logger.info("Successfully connected to MongoDB.")

            # Defaulting to a specific database name
            cls._db = cls._client["space_weather"]
            return cls._db

        except ConnectionFailure as e:
            logger.critical(f"Could not connect to MongoDB: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.critical(f"Unexpected error during database connection: {e}", exc_info=True)
            return None

    @classmethod
    def close(cls) -> None:
        """
        Closes the active MongoDB connection.

        This method should be called during application shutdown to ensure
        that sockets are released properly. It is safe to call this method
        even if no connection is active.
        """
        if cls._client:
            logger.info("Closing MongoDB connection.")
            cls._client.close()
            cls._client = None
            cls._db = None
