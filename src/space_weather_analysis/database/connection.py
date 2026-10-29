import logging
import sys
from typing import Optional, Tuple

from pydantic import ValidationError
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import ConnectionFailure

from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.config.mongo import MongoSettings

# Logger initialization for the database module
logger = logging.getLogger(__name__)


class MongoConnection:
    """Manages one MongoDB client built from the given settings.

    A service creates one instance in its entry point and passes the resulting
    database on to the components that need it. The client is created lazily
    on the first call to get_database() and released by close().
    """

    def __init__(self, settings: MongoSettings) -> None:
        """Initializes the connection without contacting the server yet.

        Args:
            settings (MongoSettings): The MongoDB settings to connect with.
        """
        self._settings = settings
        self._client: Optional[MongoClient] = None
        self._db: Optional[Database] = None

    def get_database(self) -> Optional[Database]:
        """Retrieves the configured MongoDB database.

        On the first call, a client is created and the server is pinged to verify
        that it is reachable and the authentication succeeded. Later calls return
        the cached database.

        Returns:
            Optional[Database]: The MongoDB database named by the settings if the
            connection is successful, otherwise None.
        """
        if self._db is not None:
            return self._db

        try:
            logger.info(f"Attempting to connect to MongoDB at {self._settings.host}:{self._settings.port}...")
            self._client = MongoClient(self._settings.connection_uri(), serverSelectionTimeoutMS=5000)

            # Verify that the server is actually reachable and authentication was successful.
            self._client.admin.command("ping")
            logger.info("Successfully connected to MongoDB.")

            self._db = self._client[self._settings.db_name]
            return self._db

        except ConnectionFailure as e:
            logger.critical(f"Could not connect to MongoDB: {e}", exc_info=True)
            self.close()
            return None
        except Exception as e:
            logger.critical(f"Unexpected error during database connection: {e}", exc_info=True)
            self.close()
            return None

    def close(self) -> None:
        """Closes the MongoDB client of this connection.

        This method should be called during application shutdown to ensure
        that sockets are released properly. It is safe to call this method
        even if no connection is active. A later call to get_database()
        creates a new client.
        """
        if self._client is not None:
            logger.info("Closing MongoDB connection.")
            self._client.close()
            self._client = None
            self._db = None


def connect_from_environment() -> Tuple[MongoConnection, Database]:
    """Opens the database connection of a service entry point from the MONGO_* environment variables.

    Meant for the start of a service process, which cannot work without its database.

    Returns:
        The connection, which the caller closes on shutdown, and its database.

    Raises:
        SystemExit: With status 1 if the settings are invalid or the database is unreachable; the reason is logged.
    """
    try:
        mongo_settings = MongoSettings()
    except ValidationError as e:
        logger.critical(f"Invalid MongoDB settings, check the MONGO_* environment variables: {format_settings_error(e)}. Exiting.")
        sys.exit(1)

    connection = MongoConnection(mongo_settings)
    db = connection.get_database()
    if db is None:
        logger.critical("Failed to obtain database connection. Exiting.")
        sys.exit(1)
    return connection, db
