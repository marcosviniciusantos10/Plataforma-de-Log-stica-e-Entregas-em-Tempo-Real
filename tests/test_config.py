import pytest
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import make_url

from app.core.config import Settings

STRONG = "p@ss:w/rd#1%?x"


def _settings(**overrides) -> Settings:
    values = {
        "postgres_user": "u",
        "postgres_password": STRONG,
        "postgres_db": "d",
        "postgres_host": "db",
        "redis_password": STRONG,
        "redis_host": "redis",
        "rabbitmq_user": "u",
        "rabbitmq_password": STRONG,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_database_url_escapes_special_characters():
    url = make_url(_settings().database_url)
    assert url.password == STRONG
    assert url.host == "db"
    assert url.database == "d"


def test_redis_url_escapes_special_characters():
    client = Redis.from_url(_settings().redis_url)
    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["password"] == STRONG
    assert kwargs["host"] == "redis"


def test_short_password_is_rejected():
    with pytest.raises(ValidationError):
        _settings(redis_password="")


def test_unknown_app_env_is_rejected():
    with pytest.raises(ValidationError):
        _settings(app_env="prod")


def test_secrets_are_not_shown_in_repr():
    assert STRONG not in repr(_settings())
