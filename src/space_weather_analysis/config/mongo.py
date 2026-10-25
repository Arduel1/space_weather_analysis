from urllib.parse import quote_plus

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MongoSettings(BaseSettings):
    """Typed MongoDB connection settings, read from environment variables.

    Every field is read from the environment variable 'MONGO_' followed by the
    field name in upper case, except the password, which is read from
    'MONGO_PASS'. No '.env' file is read here: the Compose services
    receive their variables via 'env_file', and tools running outside Docker must
    export them themselves.

    Each service builds its settings once in its entry point and passes them
    on to the components that need them.

    Missing credentials make the construction fail with a pydantic
    ValidationError.
    """

    model_config = SettingsConfigDict(env_prefix="MONGO_", frozen=True)

    user: str
    # The alias is the complete environment variable name; the prefix is not applied to it.
    password: SecretStr = Field(validation_alias="MONGO_PASS")
    host: str = "mongo"
    port: int = 27017
    db_name: str = "space_weather"

    def connection_uri(self) -> str:
        """Builds the MongoDB connection URI including the credentials.

        User name and password are percent-escaped as required by the MongoDB
        URI format. The result contains the plain-text password and must
        therefore never be logged.

        Returns:
            str: The URI in the form 'mongodb://user:password@host:port'.
        """
        user = quote_plus(self.user)
        password = quote_plus(self.password.get_secret_value())
        return f"mongodb://{user}:{password}@{self.host}:{self.port}"
