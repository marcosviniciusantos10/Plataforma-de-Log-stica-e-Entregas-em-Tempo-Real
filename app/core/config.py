from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _enc(value: str) -> str:
    """Escapa usuário/senha para uso seguro dentro de uma URL (ex.: @ : / # %)."""
    return quote(value, safe="")


class Settings(BaseSettings):
    """Configurações carregadas de variáveis de ambiente / arquivo .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", str_strip_whitespace=True)

    app_name: str = "logistica-api"
    app_env: Literal["development", "test", "production"] = "development"

    postgres_user: str
    postgres_password: SecretStr = Field(min_length=12)  # recusa senha curta/vazia
    postgres_db: str
    postgres_host: str = "db"
    postgres_port: int = 5432

    redis_password: SecretStr = Field(min_length=12)  # recusa senha curta/vazia
    redis_host: str = "redis"
    redis_port: int = 6379

    rabbitmq_user: str
    rabbitmq_password: SecretStr = Field(min_length=12)  # recusa senha curta/vazia
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def _pg_url(self, driver: str) -> str:
        return (
            f"postgresql+{driver}://{_enc(self.postgres_user)}:"
            f"{_enc(self.postgres_password.get_secret_value())}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url(self) -> str:
        """URL assíncrona usada pela aplicação."""
        return self._pg_url("asyncpg")

    @property
    def database_url_sync(self) -> str:
        """URL síncrona usada pelo Alembic."""
        return self._pg_url("psycopg")

    @property
    def redis_url(self) -> str:
        return (
            f"redis://:{_enc(self.redis_password.get_secret_value())}"
            f"@{self.redis_host}:{self.redis_port}/0"
        )

    @property
    def rabbitmq_url(self) -> str:
        return (
            f"amqp://{_enc(self.rabbitmq_user)}:"
            f"{_enc(self.rabbitmq_password.get_secret_value())}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}/"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
