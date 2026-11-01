from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from pymongo.database import Database

from space_weather_analysis.config.mongo import MongoSettings
from space_weather_analysis.database.connection import MongoConnection


# Runs against the throwaway MongoDB container from conftest.py.
@pytest.mark.integration
class TestMongoConnection:
    @pytest.fixture
    def connection(self, mongo_settings: MongoSettings) -> Generator[MongoConnection, None, None]:
        """Fresh connection per test, closed afterwards."""
        connection = MongoConnection(mongo_settings)
        yield connection
        connection.close()

    def test_connect_success(self, connection: MongoConnection, mongo_settings: MongoSettings) -> None:
        """get_database returns a live Database with the configured name."""
        db = connection.get_database()
        assert db is not None
        assert isinstance(db, Database)
        assert db.name == mongo_settings.db_name
        assert db.client.admin.command("ping")["ok"] == 1.0

    def test_repeated_calls_reuse_database(self, connection: MongoConnection) -> None:
        """Repeated calls return the same Database and client instance."""
        db1 = connection.get_database()
        db2 = connection.get_database()

        assert db1 is not None
        assert db2 is not None

        assert db1 is db2
        assert db1.client is db2.client

    def test_wrong_credentials(self, mongo_settings: MongoSettings) -> None:
        """get_database returns None when the server rejects the password."""
        wrong_settings = mongo_settings.model_copy(update={"password": SecretStr("definitely-not-the-password")})
        connection = MongoConnection(wrong_settings)

        assert connection.get_database() is None
        connection.close()

    def test_close_connection(self, connection: MongoConnection) -> None:
        """close() resets client and database; the next call reconnects."""
        assert connection.get_database() is not None
        assert connection._client is not None

        connection.close()

        assert connection._client is None
        assert connection._db is None

        db = connection.get_database()
        assert db is not None
        assert db.client.admin.command("ping")["ok"] == 1.0

    def test_stored_times_read_back_as_aware_utc(self, connection: MongoConnection, db: Database) -> None:
        """A stored time comes back through the connection as the same timezone-aware UTC datetime."""
        stored_time = datetime(2024, 1, 1, 12, 30, tzinfo=UTC)
        # The db fixture cleans the test database before and after the test
        db["connection_times"].insert_one({"time": stored_time})
        connection_db = connection.get_database()
        assert connection_db is not None

        read_back = connection_db["connection_times"].find_one({}, projection={"_id": 0})

        assert read_back is not None
        assert (read_back["time"], read_back["time"].tzinfo) == (stored_time, UTC)
