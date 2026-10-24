from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from space_weather_analysis.config.mongo import MongoSettings
from space_weather_analysis.database.connection import MongoConnection


@pytest.fixture
def settings() -> MongoSettings:
    """
    Settings built explicitly, independent of the surrounding environment.
    """
    # The password field is populated via its alias, which is the environment variable name.
    return MongoSettings(user="test-user", MONGO_PASS="test-secret", host="db.example", port=1234, db_name="unit_db")


@pytest.fixture
def mock_client_cls() -> Generator[MagicMock, None, None]:
    """
    Replaces MongoClient where the connection module uses it, so no server is needed.
    """
    with patch("space_weather_analysis.database.connection.MongoClient") as client_cls:
        yield client_cls


class TestMongoConnection:
    def test_no_client_before_first_use(self, settings: MongoSettings, mock_client_cls: MagicMock) -> None:
        MongoConnection(settings)

        mock_client_cls.assert_not_called()

    def test_get_database_returns_configured_database(self, settings: MongoSettings, mock_client_cls: MagicMock) -> None:
        client = mock_client_cls.return_value

        db = MongoConnection(settings).get_database()

        mock_client_cls.assert_called_once_with(settings.connection_uri(), serverSelectionTimeoutMS=5000)
        client.admin.command.assert_called_once_with("ping")
        client.__getitem__.assert_called_once_with("unit_db")
        assert db is client.__getitem__.return_value

    def test_get_database_is_cached_per_instance(self, settings: MongoSettings, mock_client_cls: MagicMock) -> None:
        connection = MongoConnection(settings)

        first = connection.get_database()
        second = connection.get_database()

        assert first is second
        mock_client_cls.assert_called_once()

    def test_instances_do_not_share_clients(self, settings: MongoSettings, mock_client_cls: MagicMock) -> None:
        MongoConnection(settings).get_database()
        MongoConnection(settings).get_database()

        assert mock_client_cls.call_count == 2

    def test_ping_failure_returns_none(self, settings: MongoSettings, mock_client_cls: MagicMock) -> None:
        client = mock_client_cls.return_value
        client.admin.command.side_effect = ServerSelectionTimeoutError("no server")

        assert MongoConnection(settings).get_database() is None
        client.close.assert_called_once()

    def test_unexpected_error_returns_none(self, settings: MongoSettings, mock_client_cls: MagicMock) -> None:
        mock_client_cls.return_value.admin.command.side_effect = RuntimeError("boom")

        assert MongoConnection(settings).get_database() is None

    def test_password_not_logged(self, settings: MongoSettings, mock_client_cls: MagicMock, caplog: pytest.LogCaptureFixture) -> None:
        mock_client_cls.return_value.admin.command.side_effect = ServerSelectionTimeoutError("no server")

        with caplog.at_level("DEBUG"):
            MongoConnection(settings).get_database()

        assert caplog.records
        assert "test-secret" not in caplog.text

    def test_close_closes_and_resets(self, settings: MongoSettings, mock_client_cls: MagicMock) -> None:
        first_client = MagicMock()
        second_client = MagicMock()
        mock_client_cls.side_effect = [first_client, second_client]
        connection = MongoConnection(settings)
        connection.get_database()

        connection.close()

        first_client.close.assert_called_once()
        # A new client is created on the next use.
        assert connection.get_database() is second_client.__getitem__.return_value

    def test_close_without_connection_is_safe(self, settings: MongoSettings, mock_client_cls: MagicMock) -> None:
        connection = MongoConnection(settings)

        connection.close()
        connection.close()

        mock_client_cls.assert_not_called()
