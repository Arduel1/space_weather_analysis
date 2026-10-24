from typing import Generator

import pytest
from pydantic import SecretStr
from pymongo.database import Database

from space_weather_analysis.config.mongo import MongoSettings
from space_weather_analysis.database.connection import MongoConnection


# Needs a running MongoDB reachable with the credentials from the environment or .env
# (see the mongo_settings fixture in conftest.py). Only the dedicated test database is used.
@pytest.mark.integration
class TestMongoConnection:
    @pytest.fixture
    def connection(self, mongo_settings: MongoSettings) -> Generator[MongoConnection, None, None]:
        """
        Provides a fresh connection per test and closes it afterwards
        to ensure isolation.
        """
        connection = MongoConnection(mongo_settings)
        yield connection
        connection.close()

    def test_connect_success(self, connection: MongoConnection, mongo_settings: MongoSettings) -> None:
        """
        Test that get_database returns the configured Database object when the settings are correct.
        """
        db = connection.get_database()
        assert db is not None
        assert isinstance(db, Database)
        assert db.name == mongo_settings.db_name

        # Verify it's actually connected
        # pymongo Database objects have a 'client' attribute
        assert db.client.admin.command("ping")["ok"] == 1.0

    def test_repeated_calls_reuse_database(self, connection: MongoConnection) -> None:
        """
        Test that multiple calls on the same connection return the exact same instance.
        """
        db1 = connection.get_database()
        db2 = connection.get_database()

        # Type narrowing assertions
        assert db1 is not None
        assert db2 is not None

        assert db1 is db2
        assert db1.client is db2.client

    def test_wrong_credentials(self, mongo_settings: MongoSettings) -> None:
        """
        Test that connection fails gracefully if the server rejects the credentials.
        """
        wrong_settings = mongo_settings.model_copy(update={"password": SecretStr("definitely-not-the-password")})
        connection = MongoConnection(wrong_settings)

        assert connection.get_database() is None
        connection.close()

    def test_close_connection(self, connection: MongoConnection) -> None:
        """
        Test that close() actually closes the client and resets the connection.
        """
        # Initialize connection
        assert connection.get_database() is not None

        # Pre-condition check
        assert connection._client is not None

        # Action
        connection.close()

        # Post-condition check
        assert connection._client is None
        assert connection._db is None

        # A new client is created on the next use
        db = connection.get_database()
        assert db is not None
        assert db.client.admin.command("ping")["ok"] == 1.0
