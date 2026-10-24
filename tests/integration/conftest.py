import os
import pathlib
from typing import Generator, Optional

import pytest
from dotenv import dotenv_values
from pymongo import MongoClient
from pymongo.database import Database

from space_weather_analysis.config.mongo import MongoSettings

# Only the integration fixtures below read the project's .env file, so unit tests never
# see the real credentials.
ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / ".env"

# Integration tests always use this dedicated database, never the configured MONGO_DB_NAME,
# because the fixtures write to it and drop its collections.
TEST_DB_NAME = "space_weather_test"


@pytest.fixture(scope="session")
def mongo_settings() -> MongoSettings:
    """
    Builds MongoDB settings for the integration tests.

    Credentials are taken from the environment if set, otherwise from the
    project's .env file. The host defaults to 'localhost' because the tests
    usually run outside the Compose network. The database is always the
    dedicated test database.
    """
    file_values = dotenv_values(ENV_FILE) if ENV_FILE.is_file() else {}

    def lookup(name: str) -> Optional[str]:
        return os.environ.get(name) or file_values.get(name)

    user = lookup("MONGO_USER")
    password = lookup("MONGO_PASS")
    if not user or not password:
        pytest.fail("Missing MongoDB credentials (MONGO_USER, MONGO_PASS) in the environment and in .env.")

    # The password field is populated via its alias, which is the environment variable name.
    return MongoSettings(
        user=user,
        MONGO_PASS=password,
        host=lookup("MONGO_HOST") or "localhost",
        port=int(lookup("MONGO_PORT") or 27017),
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
