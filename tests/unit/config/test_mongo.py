import os

import pytest
from pydantic import ValidationError

from space_weather_analysis.config.errors import format_settings_error
from space_weather_analysis.config.mongo import MongoSettings


@pytest.fixture(autouse=True)
def clean_mongo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removes all MONGO_* variables of the surrounding environment."""
    for name in list(os.environ):
        if name.upper().startswith("MONGO_"):
            monkeypatch.delenv(name)


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sets only the required credentials."""
    monkeypatch.setenv("MONGO_USER", "test-user")
    monkeypatch.setenv("MONGO_PASS", "test-secret")


class TestMongoSettings:
    def test_defaults(self, credentials: None) -> None:
        settings = MongoSettings()

        assert settings.user == "test-user"
        assert settings.password.get_secret_value() == "test-secret"
        assert settings.host == "mongo"
        assert settings.port == 27017
        assert settings.db_name == "space_weather"

    def test_env_overrides(self, credentials: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MONGO_HOST", "localhost")
        monkeypatch.setenv("MONGO_PORT", "27018")
        monkeypatch.setenv("MONGO_DB_NAME", "other_db")

        settings = MongoSettings()

        assert settings.host == "localhost"
        assert settings.port == 27018
        assert settings.db_name == "other_db"

    def test_unrelated_mongo_variables_are_ignored(self, credentials: None, monkeypatch: pytest.MonkeyPatch) -> None:
        # Compose variables that share the MONGO_ prefix but are not settings fields.
        monkeypatch.setenv("MONGO_HOST_PORT", "27019")
        monkeypatch.setenv("MONGO_EXPRESS_USER", "admin-ui")

        settings = MongoSettings()

        assert settings.port == 27017

    def test_password_is_read_from_mongo_pass_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MONGO_USER", "test-user")
        monkeypatch.setenv("MONGO_PASSWORD", "wrong-variable")

        with pytest.raises(ValidationError):
            MongoSettings()

    def test_invalid_port_raises(self, credentials: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MONGO_PORT", "not-a-number")

        with pytest.raises(ValidationError):
            MongoSettings()

    @pytest.mark.parametrize("missing", ["MONGO_USER", "MONGO_PASS"])
    def test_missing_credentials_raise(self, credentials: None, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
        monkeypatch.delenv(missing)

        with pytest.raises(ValidationError):
            MongoSettings()

    def test_connection_uri(self, credentials: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MONGO_HOST", "db.example")
        monkeypatch.setenv("MONGO_PORT", "1234")

        assert MongoSettings().connection_uri() == "mongodb://test-user:test-secret@db.example:1234"

    def test_connection_uri_escapes_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MONGO_USER", "user@domain")
        monkeypatch.setenv("MONGO_PASS", "p@ss:w/rd")

        assert MongoSettings().connection_uri() == "mongodb://user%40domain:p%40ss%3Aw%2Frd@mongo:27017"

    def test_password_not_in_repr(self, credentials: None) -> None:
        settings = MongoSettings()

        assert "test-secret" not in repr(settings)
        assert "test-secret" not in str(settings)
        assert "test-secret" not in str(settings.model_dump())

    def test_settings_are_immutable(self, credentials: None) -> None:
        settings = MongoSettings()

        with pytest.raises(ValidationError):
            settings.host = "elsewhere"


class TestFormatSettingsError:
    def test_names_missing_field_without_input_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Only the password is set, so the error input contains it.
        monkeypatch.setenv("MONGO_PASS", "test-secret")

        with pytest.raises(ValidationError) as exc_info:
            MongoSettings()

        message = format_settings_error(exc_info.value)
        assert "user" in message
        assert "Field required" in message
        assert "test-secret" not in message
