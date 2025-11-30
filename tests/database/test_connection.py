import os
from typing import Generator
from unittest.mock import patch

import pytest
from pymongo.database import Database

from space_weather_analysis.database.connection import DatabaseConnection


class TestDatabaseConnection:
    @pytest.fixture(autouse=True)
    def reset_singleton(self) -> Generator[None, None, None]:
        """
        Automatically reset the Singleton state before and after each test
        to ensure isolation.
        """
        DatabaseConnection.close()
        yield
        DatabaseConnection.close()

    def test_connect_success_local(self) -> None:
        """
        Test that get_database returns a Database object when env vars are correct.
        Assumes local .env is set up correctly for localhost.
        """
        # Ensure we are pointing to localhost for this test run
        with patch.dict(os.environ, {"MONGO_HOST": "localhost"}):
            db = DatabaseConnection.get_database()
            assert db is not None
            assert isinstance(db, Database)

            # Verify it's actually connected
            # pymongo Database objects have a 'client' attribute
            assert db.client.admin.command("ping")["ok"] == 1.0

    def test_singleton_behavior(self) -> None:
        """
        Test that multiple calls to get_database return the exact same instance.
        """
        with patch.dict(os.environ, {"MONGO_HOST": "localhost"}):
            db1 = DatabaseConnection.get_database()
            db2 = DatabaseConnection.get_database()

            # Type narrowing assertions
            assert db1 is not None
            assert db2 is not None

            assert db1 is db2
            assert db1.client is db2.client

    def test_missing_credentials(self) -> None:
        """
        Test that connection fails gracefully if credentials are missing.
        """
        # Remove MONGO_USER to simulate error
        with patch.dict(os.environ, {}, clear=True):
            # We explicitly allow MONGO_DB_NAME but remove credentials
            os.environ["MONGO_DB_NAME"] = "test_db"

            db = DatabaseConnection.get_database()
            assert db is None

    def test_missing_db_name(self) -> None:
        """
        Test that connection returns None if MONGO_DB_NAME is missing,
        even if credentials are OK.
        """
        with patch.dict(os.environ, {"MONGO_DB_NAME": ""}):
            db = DatabaseConnection.get_database()
            assert db is None

    def test_close_connection(self) -> None:
        """
        Test that close() actually closes the client and resets the singleton.
        """
        with patch.dict(os.environ, {"MONGO_HOST": "localhost"}):
            # Initialize connection
            _ = DatabaseConnection.get_database()

            # Pre-condition check
            assert DatabaseConnection._client is not None

            # Action
            DatabaseConnection.close()

            # Post-condition check
            assert DatabaseConnection._client is None
            assert DatabaseConnection._db is None
