from collections.abc import Generator
from datetime import UTC

import pytest
from pymongo import MongoClient
from pymongo.database import Database
from testcontainers.community.mongodb import MongoDbContainer

from space_weather_analysis.config.mongo import MongoSettings

# Same image as the 'mongo' service in docker-compose.yml.
MONGO_IMAGE = "mongo"

# Passed explicitly so the container ignores MONGO_INITDB_* from the environment.
CONTAINER_USER = "test"
CONTAINER_PASSWORD = "test"

# The fixtures drop collections, so only this dedicated database is used.
TEST_DB_NAME = "space_weather_test"


@pytest.fixture(scope="session")
def mongo_container() -> Generator[MongoDbContainer, None, None]:
    """Session-wide throwaway MongoDB; pinged first, as the image runs a temporary mongod during user setup."""
    with MongoDbContainer(MONGO_IMAGE, username=CONTAINER_USER, password=CONTAINER_PASSWORD) as container:
        client: MongoClient = MongoClient(container.get_connection_url(), serverSelectionTimeoutMS=30000)
        try:
            client.admin.command("ping")
        finally:
            client.close()
        yield container


@pytest.fixture(scope="session")
def mongo_settings(mongo_container: MongoDbContainer) -> MongoSettings:
    """Settings pointing to the container and the dedicated test database."""
    # The password field is only populated via its alias.
    return MongoSettings(
        user=mongo_container.username,
        MONGO_PASS=mongo_container.password,
        host=mongo_container.get_container_host_ip(),
        port=mongo_container.get_exposed_port(mongo_container.port),
        db_name=TEST_DB_NAME,
    )


@pytest.fixture(scope="session")
def mongo_client(mongo_settings: MongoSettings) -> Generator[MongoClient, None, None]:
    """Session-wide client for the container; it reads stored times as timezone-aware UTC datetimes, as the services do."""
    client: MongoClient = MongoClient(mongo_settings.connection_uri(), serverSelectionTimeoutMS=2000, tz_aware=True, tzinfo=UTC)

    try:
        client.admin.command("ping")
        yield client
    finally:
        client.close()


@pytest.fixture(scope="function")
def db(mongo_client: MongoClient) -> Generator[Database, None, None]:
    """Test database, cleaned before and after each test."""
    database = mongo_client[TEST_DB_NAME]

    _clean_database(database)

    yield database

    _clean_database(database)


def _clean_database(database: Database) -> None:
    """Drops all collections except 'system.*', including the master catalog, so each test starts empty."""
    if database.name != TEST_DB_NAME:
        raise RuntimeError(f"Refusing to clean database '{database.name}': only '{TEST_DB_NAME}' may be cleaned.")

    for collection_name in database.list_collection_names():
        if not collection_name.startswith("system."):
            database[collection_name].drop()
