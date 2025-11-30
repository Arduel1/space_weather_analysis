import os
from typing import Generator

import pytest
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.database import Database

# Load environment variables from .env file
load_dotenv()


@pytest.fixture(scope="session")
def mongo_client() -> Generator[MongoClient, None, None]:
    """
    Creates a MongoDB connection for the entire test session.
    Relies on environment variables loaded from .env.
    """
    user = os.getenv("MONGO_USER")
    pw = os.getenv("MONGO_PASS")
    port = os.getenv("MONGO_PORT")

    # Host is vital for switching between Docker ('mongo') and local ('localhost')
    host = os.getenv("MONGO_HOST", "localhost")

    if not all([user, pw, port]):
        pytest.fail("Missing MongoDB credentials in environment variables (MONGO_USER, MONGO_PASS, MONGO_PORT).")

    uri = f"mongodb://{user}:{pw}@{host}:{port}/"
    client: MongoClient = MongoClient(uri, serverSelectionTimeoutMS=2000)

    try:
        # Check if the database is reachable
        client.admin.command("ping")
        yield client
    finally:
        client.close()


@pytest.fixture(scope="function")
def db(mongo_client: MongoClient) -> Generator[Database, None, None]:
    """
    Provides a clean database instance for each test function.
    Drops all collections after the test to prevent side effects,
    EXCEPT for critical collections like 'master_catalog'.
    """
    db_name = os.getenv("MONGO_DB_NAME")
    if not db_name:
        pytest.fail("MONGO_DB_NAME not set in environment.")

    database = mongo_client[db_name]

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
    preserved_collections = {"master_catalog"}

    for collection_name in database.list_collection_names():
        if collection_name not in preserved_collections and not collection_name.startswith("system."):
            database[collection_name].drop()
