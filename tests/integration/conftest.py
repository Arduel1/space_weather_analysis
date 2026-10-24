from typing import Generator

import pytest
from pymongo import MongoClient
from pymongo.database import Database
from testcontainers.community.mongodb import MongoDbContainer

from space_weather_analysis.config.mongo import MongoSettings

# The same image reference as the 'mongo' service in docker-compose.yml.
MONGO_IMAGE = "mongo"

# Root credentials of the throwaway container. They are passed explicitly so that
# the container never picks up MONGO_INITDB_* variables from the environment.
CONTAINER_USER = "test"
CONTAINER_PASSWORD = "test"

# Integration tests always use this dedicated database, because the fixtures
# write to it and drop its collections.
TEST_DB_NAME = "space_weather_test"


@pytest.fixture(scope="session")
def mongo_container() -> Generator[MongoDbContainer, None, None]:
    """
    Starts a throwaway MongoDB container for the entire test session.

    The container is started via Docker and removed at the end of the session.
    Before it is handed out, the server is pinged until it accepts authenticated
    connections, because the image runs a temporary mongod while it creates the root user.
    """
    with MongoDbContainer(MONGO_IMAGE, username=CONTAINER_USER, password=CONTAINER_PASSWORD) as container:
        client: MongoClient = MongoClient(container.get_connection_url(), serverSelectionTimeoutMS=30000)
        try:
            client.admin.command("ping")
        finally:
            client.close()
        yield container


@pytest.fixture(scope="session")
def mongo_settings(mongo_container: MongoDbContainer) -> MongoSettings:
    """
    Builds MongoDB settings that point to the throwaway container.

    Host, mapped port and root credentials are taken from the container. The
    database is always the dedicated test database.
    """
    # The password field is populated via its alias, which is the environment variable name.
    return MongoSettings(
        user=mongo_container.username,
        MONGO_PASS=mongo_container.password,
        host=mongo_container.get_container_host_ip(),
        port=mongo_container.get_exposed_port(mongo_container.port),
        db_name=TEST_DB_NAME,
    )


@pytest.fixture(scope="session")
def mongo_client(mongo_settings: MongoSettings) -> Generator[MongoClient, None, None]:
    """
    Creates a MongoDB connection for the entire test session.
    Relies on the settings from the mongo_settings fixture.
    """
    client: MongoClient = MongoClient(mongo_settings.connection_uri(), serverSelectionTimeoutMS=2000)

    try:
        # Check if the database is reachable
        client.admin.command("ping")
        yield client
    finally:
        client.close()


@pytest.fixture(scope="function")
def db(mongo_client: MongoClient) -> Generator[Database, None, None]:
    """
    Provides a clean test database instance for each test function.
    Drops all collections after the test to prevent side effects,
    EXCEPT for critical collections like 'master_catalog'.
    Always uses the dedicated test database, never the configured MONGO_DB_NAME.
    """
    database = mongo_client[TEST_DB_NAME]

    # Pre-test cleanup
    _clean_database(database)

    yield database

    # Post-test cleanup
    _clean_database(database)


def _clean_database(database: Database) -> None:
    """
    Helper function to drop non-essential collections in the database.

    Preserves:
    - master_catalog: Required for discovery/pipeline tests.
    - system.*: Internal MongoDB collections.
    """
    if database.name != TEST_DB_NAME:
        raise RuntimeError(f"Refusing to clean database '{database.name}': only '{TEST_DB_NAME}' may be cleaned.")

    preserved_collections = {"master_catalog"}

    for collection_name in database.list_collection_names():
        if collection_name not in preserved_collections and not collection_name.startswith("system."):
            database[collection_name].drop()
